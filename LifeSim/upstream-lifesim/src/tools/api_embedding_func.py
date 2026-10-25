"""API vector generation for the native LifeSim DenseRetriever / Chroma chain."""
import math
from openai import OpenAI
from chromadb.api.types import EmbeddingFunction, Documents


class EmbeddingServiceError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class ZhipuEmbeddingFunction(EmbeddingFunction[Documents]):
    provider_label = "智谱"
    def __init__(self, api_key: str, model="embedding-3", dimensions=512, client=None):
        if not api_key:
            raise ValueError("请在本地密钥文件的 zhipu_api_key 中填写智谱密钥")
        if model != "embedding-3" or dimensions not in (256, 512, 1024, 2048):
            raise ValueError("embedding-3 的维度必须为 256、512、1024 或 2048")
        self.model, self.dimensions = model, dimensions
        self.client = client or OpenAI(api_key=api_key, base_url="https://open.bigmodel.cn/api/paas/v4/",
                                       timeout=45, max_retries=2)

    def __call__(self, input):
        documents = list(input)
        if any(not isinstance(text, str) or not text.strip() for text in documents):
            raise ValueError("嵌入输入必须是非空文本")
        vectors = []
        for start in range(0, len(documents), 64):
            batch = documents[start:start + 64]
            try:
                response = self.client.embeddings.create(**self.request_options(batch))
            except Exception as error:
                code = getattr(error, "status_code", None)
                if code in (401, 403):
                    raise EmbeddingServiceError(f"{self.provider_label}嵌入服务HTTP{code}：密钥或权限未被服务接受", code) from None
                raise EmbeddingServiceError(f"{self.provider_label}嵌入请求失败，请检查额度、文本长度和网络", code) from None
            data = sorted(response.data, key=lambda item: item.index)
            if [item.index for item in data] != list(range(len(batch))):
                raise ValueError(f"{self.provider_label}返回的嵌入编号或数量不完整")
            for item in data:
                vector = list(item.embedding)
                if len(vector) != self.dimensions or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
                    raise ValueError(f"{self.provider_label}返回的向量维度或数值无效")
                vectors.append(vector)
        return vectors

    def request_options(self, batch):
        return dict(model=self.model, input=batch, dimensions=self.dimensions, encoding_format="float")

    def embed_query(self, input):
        return self(input)

    def name(self):
        return "lifesim-zhipu-embedding-v1"

    def get_config(self):
        # Chroma configuration never contains the API key.
        return {"model": self.model, "dimensions": self.dimensions}


class SiliconFlowEmbeddingFunction(ZhipuEmbeddingFunction):
    """API embeddings injected into LifeSim's native Chroma retriever.

    BGE-M3 has a fixed 1024 dimensions; its API must not receive `dimensions`.
    """
    provider_label = "硅基流动"

    def __init__(self, api_key: str, model="BAAI/bge-m3", dimensions=1024, client=None):
        if not api_key:
            raise ValueError("请在本地密钥文件的 siliconflow_api_key 中填写硅基流动密钥")
        if model != "BAAI/bge-m3" or dimensions != 1024:
            raise ValueError("当前硅基流动记忆适配使用 BAAI/bge-m3，向量维度为1024")
        self.model, self.dimensions = model, dimensions
        self.client = client or OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1",
                                       timeout=45, max_retries=1)

    def request_options(self, batch):
        return dict(model=self.model, input=batch, encoding_format="float")

    def name(self):
        return "lifesim-siliconflow-embedding-v1"
