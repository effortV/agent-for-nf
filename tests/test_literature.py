import asyncio
import json

import httpx
from app.config import Settings
from app.services.literature import LiteratureCandidate, LiteratureDiscovery, usable_fulltext_hint


def test_merge_cross_source_candidate() -> None:
    a = LiteratureCandidate.create(
        source="crossref",
        title="Nanofiltration membrane for lithium separation",
        authors=[{"name": "Zhang"}],
        doi="10.1000/NF1",
    )
    b = LiteratureCandidate.create(
        source="openalex",
        title="Nanofiltration membrane for lithium separation",
        authors=[{"name": "Zhang"}],
        doi="https://doi.org/10.1000/nf1",
        openalex_id="https://openalex.org/W1",
        abstract="Li Mg separation by nanofiltration",
    )
    merged = LiteratureDiscovery._merge([[a], [b]])
    assert len(merged) == 1
    assert merged[0].openalex_id == "W1"
    assert merged[0].abstract


def test_doi_landing_page_is_not_mislabeled_as_a_fulltext_pdf() -> None:
    assert usable_fulltext_hint("https://doi.org/10.1186/example") is None
    assert usable_fulltext_hint("https://www.semanticscholar.org/paper/123") is None
    assert usable_fulltext_hint("https://link.springer.com/content/pdf/10.1186/example.pdf")


def test_relevance_score_prefers_domain_title() -> None:
    relevant = LiteratureCandidate.create(
        source="test",
        title="Nanofiltration for Li/Mg separation",
        authors=[],
        abstract="lithium recovery",
    )
    other = LiteratureCandidate.create(source="test", title="Lithium battery cathode", authors=[], abstract="materials")
    expanded = {"zh": [], "en": ["lithium recovery", "Li/Mg separation"], "abbreviations": [], "materials": [], "methods": [], "systems": [], "metrics": []}
    LiteratureDiscovery._score([relevant, other], expanded)
    assert relevant.relevance_score > other.relevance_score


def test_connector_auth_headers_are_added() -> None:
    discovery = LiteratureDiscovery(
        Settings(
            openalex_api_key="openalex-test",
            openalex_email="researcher@example.org",
            elsevier_api_key="elsevier-test",
            elsevier_insttoken="institution-test",
        )
    )
    try:
        params = discovery._add_openalex_auth({"search": "nanofiltration"})
        assert params["api_key"] == "openalex-test"
        assert params["mailto"] == "researcher@example.org"
        headers = discovery._elsevier_headers("application/json")
        assert headers["X-ELS-APIKey"] == "elsevier-test"
        assert headers["X-ELS-Insttoken"] == "institution-test"
    finally:
        asyncio.run(discovery.close())


def test_safe_error_never_contains_query_secrets() -> None:
    request = httpx.Request("GET", "https://api.openalex.org/works?api_key=secret-value&search=nf")
    response = httpx.Response(401, request=request)
    error = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    safe = LiteratureDiscovery._safe_error(error)
    assert safe["status_code"] == 401
    assert "secret-value" not in json.dumps(safe)
    assert "?" not in safe["endpoint"]


def test_transient_429_is_retried_and_auth_error_opens_circuit() -> None:
    async def exercise() -> None:
        attempts = {"crossref": 0, "elsevier": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if "crossref" in request.url.host:
                attempts["crossref"] += 1
                status = 429 if attempts["crossref"] == 1 else 200
                return httpx.Response(status, request=request, json={})
            attempts["elsevier"] += 1
            return httpx.Response(401, request=request, json={})

        discovery = LiteratureDiscovery(Settings())
        await discovery.client.aclose()
        discovery.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        discovery._source_limits["crossref"] = (asyncio.Semaphore(1), 0.0)
        discovery._source_limits["elsevier"] = (asyncio.Semaphore(1), 0.0)
        try:
            response = await discovery._request("crossref", "https://api.crossref.org/works", max_attempts=2)
            assert response.status_code == 200
            assert attempts["crossref"] == 2
            for _ in range(2):
                try:
                    await discovery._request("elsevier", "https://api.elsevier.com/content/search/sciencedirect")
                except httpx.HTTPStatusError as exc:
                    assert exc.response.status_code == 401
                    safe = discovery._safe_error(exc)
                    if attempts["elsevier"] == 1 and "circuit open" in str(exc):
                        assert safe["type"] == "circuit_open"
            assert attempts["elsevier"] == 1
        finally:
            await discovery.close()

    asyncio.run(exercise())
