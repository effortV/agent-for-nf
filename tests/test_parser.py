from app.services.parser import DocumentParser, ParsedBlock, ParsedDocument, chunk_document


def test_chunk_document_preserves_page_and_overlap() -> None:
    parsed = ParsedDocument(
        title="test",
        parser="test",
        blocks=[ParsedBlock(kind="section", text="A" * 300 + "。" + "B" * 300, section="Results", page=7)],
    )
    chunks = chunk_document(parsed, chunk_size=350, overlap=50)
    assert len(chunks) == 2
    assert all(item["page_start"] == 7 for item in chunks)
    assert all(item["section"] == "Results" for item in chunks)


def test_html_parser_preserves_sections_tables_and_captions(tmp_path) -> None:
    path = tmp_path / "article.html"
    path.write_text(
        """
        <html><head><meta name="citation_title" content="Open NF article"></head>
        <body><article><h2>Experimental</h2><p>PIP and TMC formed a polyamide layer.</p>
        <table id="table-1"><tr><th>Pressure</th><th>Flux</th></tr><tr><td>6 bar</td><td>42 LMH</td></tr></table>
        <figure id="fig-1"><figcaption>Membrane morphology.</figcaption></figure></article></body></html>
        """,
        encoding="utf-8",
    )
    parsed = DocumentParser().parse(path, "text/html")
    assert parsed.parser == "public-html"
    assert parsed.title == "Open NF article"
    assert any(block.kind == "table" and "42 LMH" in block.text for block in parsed.blocks)
    assert any(block.kind == "figure_caption" for block in parsed.blocks)
