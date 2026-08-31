from app.services.vocab import VocabularyExpander


class NoLLM:
    configured = False


def test_local_expansion_for_lithium() -> None:
    expander = VocabularyExpander(llm=NoLLM())
    terms = expander.local_expand("盐湖提锂")
    assert "nanofiltration" in terms["en"]
    assert "Li/Mg separation" in terms["en"]
    assert "Li+/Mg2+ selectivity" in terms["metrics"]


def test_search_queries_are_domain_anchored() -> None:
    expander = VocabularyExpander(llm=NoLLM())
    queries = expander.search_queries(expander.local_expand("抗污染膜"))
    assert queries
    assert any("nanofiltration" in query for query in queries)


def test_machine_learning_expansion_searches_the_intersection() -> None:
    expander = VocabularyExpander(llm=NoLLM())
    terms = expander.local_expand("帮我梳理纳滤领域和机器学习相关研究的进展")
    queries = expander.search_queries(terms)
    assert "machine learning" in terms["en"]
    assert "artificial neural network" in terms["en"]
    assert any("nanofiltration" in query and "machine learning" in query for query in queries)
    assert not any(query == "nanofiltration" for query in queries)
