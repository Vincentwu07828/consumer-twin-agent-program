"""Subprocess probe: real LifeSim DenseRetriever + Chroma, fixture vector generation only."""
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "LifeSim/upstream-lifesim/src"))


class FixtureEmbedding:
    def __init__(self, **kwargs):
        pass

    def __call__(self, input):
        return [[byte / 255 for byte in hashlib.sha256(text.encode()).digest()[:16]] for text in input]

    def name(self):
        return "fixture-embedding"

    def embed_query(self, input):
        return self(input)

    def get_config(self):
        return {}

    def is_legacy(self):
        return True


def main():
    from tools.dense_retriever import DenseRetriever
    from agents.memory import KVMemory
    with patch("tools.dense_retriever.MTSentenceTransformerEmbeddingFunction", FixtureEmbedding):
        retriever = DenseRetriever(model_name="fixture", embedding_dim=16,
            collection_name="consumer_native_chroma_test", persist_directory=sys.argv[1], logger_silent=True)
        memory = KVMemory({}, retriever=retriever)
        memory.add_item({"query": "observed-tea", "response": "明确喜欢茶", "source": "real"})
        matches = memory.search("明确喜欢茶", top_k=1)
        assert matches[0][0]["response"] == "明确喜欢茶"
        assert matches[0][0]["source"] == "real"
    from tools.api_embedding_func import ZhipuEmbeddingFunction
    class FixtureApiClient:
        @property
        def embeddings(self):
            return self
        def create(self, **kwargs):
            return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[.1] * kwargs["dimensions"])
                                         for i, _ in enumerate(kwargs["input"])])
    api_embeddings = ZhipuEmbeddingFunction("fixture-not-real", client=FixtureApiClient())
    api_retriever = DenseRetriever(model_name="embedding-3", embedding_dim=512,
        collection_name="consumer_native_zhipu_fixture_test", persist_directory=sys.argv[1],
        logger_silent=True, embedding_function=api_embeddings)
    api_memory = KVMemory({}, retriever=api_retriever)
    api_memory.add_item({"query": "api-feedback", "response": "明确偏好", "source": "real"})
    assert api_memory.search("偏好", top_k=1)[0][0]["response"] == "明确偏好"
    print("native_lifesim_retriever_ok")


if __name__ == "__main__":
    main()
