from app.services.dedupe import extract_doi, normalize_doi, normalize_openalex_id, sha256_bytes, title_author_fingerprint


def test_normalize_doi_variants() -> None:
    assert normalize_doi("https://doi.org/10.1016/J.MEMSCI.2024.12345.") == "10.1016/j.memsci.2024.12345"
    assert normalize_doi("DOI: 10.1000/XYZ") == "10.1000/xyz"
    assert normalize_doi(None) is None


def test_openalex_and_fingerprint_are_stable() -> None:
    assert normalize_openalex_id("https://openalex.org/w123/") == "W123"
    left = title_author_fingerprint("A NF membrane", [{"name": "Li Ming"}])
    right = title_author_fingerprint("A  NF-Membrane", [{"display_name": "Li Ming"}])
    assert left == right


def test_sha256_bytes() -> None:
    assert sha256_bytes(b"nanofiltration") == sha256_bytes(b"nanofiltration")
    assert sha256_bytes(b"nanofiltration") != sha256_bytes(b"microfiltration")


def test_extract_doi_from_article_url_or_citation() -> None:
    assert extract_doi("https://example.org/article?doi=10.1016/j.memsci.2024.123456") == "10.1016/j.memsci.2024.123456"
    assert extract_doi("No DOI here") is None
