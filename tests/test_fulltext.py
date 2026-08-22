import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.services.fulltext import ElsevierEntitlementError, FullTextResolver, _ROBOTS_CACHE
from app.services.pipeline import content_mode_label, document_processing_priority, update_current_document


async def _allow_test_url(_url: str) -> None:
    return None


def _article_html(*, pdf_meta: bool = False) -> bytes:
    pdf = '<meta name="citation_pdf_url" content="/paper.pdf">' if pdf_meta else ""
    paragraphs = "".join(f"<p>Nanofiltration full text paragraph {index} " + "evidence " * 20 + "</p>" for index in range(8))
    return (
        f"<html><head><meta name='citation_title' content='Public NF paper'>"
        f"<meta name='citation_doi' content='10.1000/nf.1'>{pdf}</head>"
        f"<body><article><h2>Introduction</h2>{paragraphs}<h2>Experimental</h2>"
        "<p>Methods and conditions are reported.</p><h2>Results</h2><p>Flux increased.</p>"
        "</article></body></html>"
    ).encode()


def test_direct_page_discovers_and_downloads_pdf() -> None:
    async def exercise() -> None:
        _ROBOTS_CACHE.clear()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404, request=request)
            if request.url.path == "/paper.pdf":
                return httpx.Response(200, request=request, content=b"%PDF-1.7\npublic", headers={"content-type": "application/pdf"})
            return httpx.Response(200, request=request, content=_article_html(pdf_meta=True), headers={"content-type": "text/html"})

        resolver = FullTextResolver(Settings(direct_web_min_interval_seconds=0, direct_html_min_chars=100))
        await resolver.client.aclose()
        resolver.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        resolver._validate_public_url = _allow_test_url  # type: ignore[method-assign]
        try:
            result = await resolver.resolve_public_source("https://example.org/article")
            assert result.extension == "pdf"
            assert result.title == "Public NF paper"
            assert result.doi == "10.1000/nf.1"
        finally:
            await resolver.close()

    asyncio.run(exercise())


def test_doi_uses_europe_pmc_xml_when_publisher_page_blocks_robots() -> None:
    async def exercise() -> None:
        doi = "10.1186/s13065-024-01211-5"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/search"):
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "resultList": {
                            "result": [
                                {
                                    "doi": doi,
                                    "pmcid": "PMC11212259",
                                    "title": "Open nanofiltration paper",
                                    "license": "CC BY",
                                }
                            ]
                        }
                    },
                )
            if request.url.path.endswith("/PMC11212259/fullTextXML"):
                return httpx.Response(
                    200,
                    request=request,
                    content=b"<?xml version='1.0'?><article><body><sec>Full text</sec></body></article>",
                    headers={"content-type": "application/xml"},
                )
            raise AssertionError(str(request.url))

        resolver = FullTextResolver(Settings(direct_web_min_interval_seconds=0))
        await resolver.client.aclose()
        resolver.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        try:
            result = await resolver.resolve_public_source(f"https://doi.org/{doi}")
            assert result.extension == "xml"
            assert result.source == "europe-pmc-xml"
            assert result.doi == doi
            assert result.license == "CC BY"
        finally:
            await resolver.close()

    asyncio.run(exercise())


def test_public_html_fulltext_is_accepted_but_abstract_page_is_rejected() -> None:
    info = FullTextResolver.inspect_html(_article_html(), "https://example.org/article", min_chars=100)
    assert info.looks_like_fulltext
    abstract = b"<html><body><main><h1>Abstract</h1><p>Only a short abstract is available.</p></main></body></html>"
    short = FullTextResolver.inspect_html(abstract, "https://example.org/abstract", min_chars=100)
    assert not short.looks_like_fulltext


def test_private_network_urls_are_rejected() -> None:
    async def exercise() -> None:
        resolver = FullTextResolver(Settings())
        try:
            with pytest.raises(ValueError, match="内网"):
                await resolver._validate_public_url("http://127.0.0.1/private.pdf")
        finally:
            await resolver.close()

    asyncio.run(exercise())


def test_elsevier_requests_explicit_full_view_and_accepts_body_xml() -> None:
    async def exercise() -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["view"] = request.url.params.get("view", "")
            captured["accept"] = request.headers.get("accept", "")
            captured["api_key"] = request.headers.get("x-els-apikey", "")
            captured["resource_version"] = request.headers.get("x-els-resourceversion", "")
            captured["insttoken"] = request.headers.get("x-els-insttoken", "")
            body = ("Elsevier nanofiltration full text " * 40).encode()
            content = b"<full-text-retrieval-response><originalText>" + body + b"</originalText></full-text-retrieval-response>"
            return httpx.Response(200, request=request, content=content, headers={"content-type": "text/xml"})

        resolver = FullTextResolver(Settings(elsevier_api_key="key-test", elsevier_insttoken="inst-test"))
        await resolver.client.aclose()
        resolver.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await resolver._from_elsevier("10.1000/nf.1")
            assert result is not None
            assert result.source == "sciencedirect-tdm"
            assert captured == {
                "view": "FULL",
                "accept": "text/xml",
                "api_key": "key-test",
                "resource_version": "new",
                "insttoken": "inst-test",
            }
        finally:
            await resolver.close()

    asyncio.run(exercise())


def test_elsevier_meta_abs_is_not_mislabeled_as_fulltext() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            content = b"<full-text-retrieval-response><coredata><description>Abstract only</description></coredata></full-text-retrieval-response>"
            return httpx.Response(200, request=request, content=content, headers={"content-type": "text/xml"})

        resolver = FullTextResolver(Settings(elsevier_api_key="key-test"))
        await resolver.client.aclose()
        resolver.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(ElsevierEntitlementError, match="META_ABS"):
                await resolver._from_elsevier("10.1000/nf.1")
        finally:
            await resolver.close()

    asyncio.run(exercise())


def test_elsevier_403_reports_upstream_service_code() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                request=request,
                json={
                    "service-error": {
                        "status": {
                            "statusCode": "AUTHENTICATION_ERROR",
                            "statusText": "Requestor configuration settings insufficient",
                        }
                    }
                },
            )

        resolver = FullTextResolver(Settings(elsevier_api_key="key-test"))
        await resolver.client.aclose()
        resolver.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(ElsevierEntitlementError, match="AUTHENTICATION_ERROR"):
                await resolver._from_elsevier("10.1000/nf.1")
        finally:
            await resolver.close()

    asyncio.run(exercise())


def test_elsevier_pii_is_extracted_from_sciencedirect_urls() -> None:
    assert (
        FullTextResolver.extract_elsevier_pii(
            "https://www.sciencedirect.com/science/article/pii/S0376738825001234"
        )
        == "S0376738825001234"
    )
    assert FullTextResolver.extract_elsevier_pii("https://example.org/article") is None


def test_saved_or_public_fulltext_is_processed_before_metadata_only_candidates() -> None:
    def document(*, object_key=None, fulltext_url=None, is_open_access=False, doi=None, score=0):
        return SimpleNamespace(
            object_key=object_key,
            fulltext_url=fulltext_url,
            is_open_access=is_open_access,
            doi_normalized=doi,
            relevance_score=score,
        )

    rows = [
        document(doi="10.1/metadata", score=99),
        document(is_open_access=True, doi="10.1/open"),
        document(fulltext_url="https://example.org/paper.pdf"),
        document(object_key="kb/doc/source.pdf"),
    ]
    ordered = sorted(rows, key=document_processing_priority)
    assert ordered[0].object_key
    assert ordered[1].fulltext_url.endswith(".pdf")
    assert ordered[2].is_open_access


def test_current_document_progress_records_mode_position_and_counts() -> None:
    assert content_mode_label(SimpleNamespace(extension="pdf")) == "全文 PDF"
    job = SimpleNamespace(counts={}, stage="", progress=0.0)
    document = SimpleNamespace(
        id="doc-1",
        title="NF paper",
        doi_normalized="10.1000/nf",
        status=SimpleNamespace(value="parsed"),
        fulltext_source="europe-pmc-xml",
        object_key="kb/source.xml",
    )
    update_current_document(
        job,
        document,
        position=2,
        total=10,
        stage="切片完成",
        document_progress=0.5,
        content_mode="全文 XML/JATS",
        chunks=18,
        facts=7,
    )
    current = job.counts["current_document"]
    assert current["title"] == "NF paper"
    assert current["position"] == 2
    assert current["content_mode"] == "全文 XML/JATS"
    assert current["chunks"] == 18
    assert current["facts"] == 7
    assert 0.25 < job.progress < 1
