"""Bounded embedding diagnostic; never prints keys, request headers or provider error bodies."""
import json
import sys
from pathlib import Path
import _bootstrap
sys.path.insert(0, str(_bootstrap.ROOT / "LifeSim/upstream-lifesim/src"))
from consumer_behavior.research_service import ConsumerResearchService
from consumer_behavior.storage import EventStore
from tools.api_embedding_func import SiliconFlowEmbeddingFunction, ZhipuEmbeddingFunction
from openai import OpenAI


def main():
    config = json.loads((_bootstrap.ROOT / "config/consumer_lifesim.local.json").read_text(encoding="utf-8-sig"))
    credential = json.loads((_bootstrap.ROOT / config["retriever"]["api_key_file"]).read_text(encoding="utf-8-sig"))
    settings = config["retriever"]
    key = credential[settings.get("api_key_field", "zhipu_api_key")].strip()
    try:
        cls = SiliconFlowEmbeddingFunction if settings["provider"] == "siliconflow" else ZhipuEmbeddingFunction
        function = cls(key, model=settings["model_name"], dimensions=settings["embedding_dim"])
        result = function(["集成检查"])
        print(json.dumps({"step":"provider_embedding", "provider":settings["provider"], "dimensions":len(result[0])}), flush=True)
    except Exception as error:
        print(json.dumps({"step":"provider_embedding", "type":type(error).__name__, "status_code":getattr(error,"status_code",None)}), flush=True)
        if settings["provider"] == "siliconflow" and getattr(error, "status_code", None) == 402:
            client = OpenAI(api_key=key, base_url="https://api.siliconflow.cn/v1", timeout=30, max_retries=0)
            try:
                client.embeddings.create(model=settings["model_name"], input=["连接检查"], encoding_format="float")
            except Exception as diagnostic:
                body = getattr(diagnostic, "body", {})
                if isinstance(body, dict):
                    message = str(body.get("message", body.get("error", ""))).replace(key, "[redacted]")
                    print(json.dumps({"step":"provider_quota_diagnostic", "status_code":getattr(diagnostic,"status_code",None),
                        "code":body.get("code"), "message":message[:200]},ensure_ascii=True),flush=True)
    store = EventStore(_bootstrap.ROOT / "runtime/consumer_research.sqlite3")
    row = store.connection.execute("SELECT payload FROM research_sessions WHERE json_extract(payload, '$.purpose')='integration_probe' ORDER BY rowid DESC LIMIT 1").fetchone()
    if row:
        s = json.loads(row[0])
        service = ConsumerResearchService(config, store)
        try:
            # First create native runtime without queued memories, then call the original index directly.
            seed = dict(s, memories=[])
            runtime = service._runtime(seed)
            for memory in s["memories"][:1]:
                runtime.assistant_agent.dynamic_memory.add_item({"query":memory["id"], "response":memory["text"],
                    "source":memory["source"], "evidence_id":memory["evidence_id"]})
            print(json.dumps({"step":"native_chroma_memory", "status":"ok"}), flush=True)
        except Exception as error:
            # Only class and a redacted operational message; fixture documents may appear in Chroma errors.
            safe = str(error).replace(key, "[redacted]")
            print(json.dumps({"step":"native_chroma_memory", "type":type(error).__name__, "message":safe[:500]},ensure_ascii=True), flush=True)
    store.close()


if __name__ == "__main__":
    main()
