import sys
from types import ModuleType
from types import SimpleNamespace

from app.config import Settings
from app.services.vector_store import EmbeddingProvider, VectorStore


class _Client:
    def __init__(self, values):
        self.values = values

    def list_collections(self):
        return self.values


def test_collection_names_support_chroma_06_strings() -> None:
    assert VectorStore._collection_names(_Client(["alpha", "beta"])) == {"alpha", "beta"}


def test_collection_names_support_legacy_collection_objects() -> None:
    assert VectorStore._collection_names(
        _Client([SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")])
    ) == {"alpha", "beta"}


def test_embedding_loader_uses_complete_local_cache_before_network(monkeypatch) -> None:
    calls = []
    module = ModuleType("sentence_transformers")

    def fake_model(_name, *, device, local_files_only):
        calls.append((device, local_files_only))
        return object()

    module.SentenceTransformer = fake_model
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    provider = EmbeddingProvider(
        Settings(
            embedding_model="test/local-first-bge",
            embedding_device="cpu",
            allow_embedding_download=True,
        )
    )
    provider._load()
    assert calls == [("cpu", True)]
