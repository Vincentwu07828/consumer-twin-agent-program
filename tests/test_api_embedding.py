from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "LifeSim/upstream-lifesim/src"))
from tools.api_embedding_func import ZhipuEmbeddingFunction, SiliconFlowEmbeddingFunction, EmbeddingServiceError


class ApiEmbeddingTests(unittest.TestCase):
    def test_batching_and_order_are_preserved(self):
        client = Mock()
        def response(**kwargs):
            return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[float(i)] * 256)
                                         for i in reversed(range(len(kwargs["input"])))])
        client.embeddings.create.side_effect = response
        function = ZhipuEmbeddingFunction("fixture-key", dimensions=256, client=client)
        result = function(["历史反馈"] * 65)
        self.assertEqual(len(result), 65)
        self.assertEqual(client.embeddings.create.call_count, 2)
        self.assertEqual(result[1][0], 1)
        self.assertNotIn("api_key", function.get_config())

    def test_invalid_response_rejected(self):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0])])
        with self.assertRaises(ValueError):
            ZhipuEmbeddingFunction("fixture", client=client)(["记忆"])

    def test_query_uses_same_model_and_dimensions(self):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[.1] * 512)])
        function = ZhipuEmbeddingFunction("fixture", client=client)
        self.assertEqual(len(function.embed_query(["需求"])[0]), 512)
        self.assertEqual(client.embeddings.create.call_args.kwargs["model"], "embedding-3")
        self.assertEqual(client.embeddings.create.call_args.kwargs["dimensions"], 512)

    def test_missing_key_and_invalid_input_are_explicit(self):
        with self.assertRaises(ValueError):
            ZhipuEmbeddingFunction("")
        with self.assertRaises(ValueError):
            ZhipuEmbeddingFunction("fixture", client=Mock())([""])

    def test_siliconflow_fixed_dimensions_and_no_secret_in_config(self):
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[.1] * 1024)])
        function = SiliconFlowEmbeddingFunction("fixture-secret", client=client)
        self.assertEqual(len(function.embed_query(["已确认偏好"])[0]), 1024)
        request = client.embeddings.create.call_args.kwargs
        self.assertEqual(request["model"], "BAAI/bge-m3")
        self.assertNotIn("dimensions", request)
        self.assertNotIn("fixture-secret", str(function.get_config()))

    def test_provider_errors_are_sanitized_and_identified(self):
        client = Mock()
        error = RuntimeError("fixture-secret in provider body")
        error.status_code = 401
        client.embeddings.create.side_effect = error
        with self.assertRaises(EmbeddingServiceError) as context:
            SiliconFlowEmbeddingFunction("fixture-secret", client=client)(["记忆"])
        self.assertEqual(context.exception.status_code, 401)
        self.assertIn("硅基流动", str(context.exception))
        self.assertNotIn("fixture-secret", str(context.exception))


if __name__ == "__main__":
    unittest.main()
