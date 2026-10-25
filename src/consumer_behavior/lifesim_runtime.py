"""Assembly only: primary persona, agents and memory implementations live in LifeSim."""
import json
import os
import sys
import hashlib
from pathlib import Path
from uuid import uuid4


def create_simulator(config: dict, *, user_model=None, assistant_model=None, retriever_factory=None,
                     behavior_model=None, store=None):
    root = Path(__file__).resolve().parents[2]
    upstream_src = root / "LifeSim" / "upstream-lifesim" / "src"
    if not (upstream_src / "profiles/profile_generator.py").is_file():
        raise FileNotFoundError("缺少 LifeSim 源码，请先运行 scripts/restore_lifesim.py")
    if str(upstream_src) not in sys.path:
        sys.path.insert(0, str(upstream_src))
    from agents.memory import KVMemory
    from agents.consumer_agent import ConsumerUserAgent, ConsumerAssistantAgent
    from engine.event_engine import OfflineLifeEventEngine
    from models.cloud import DeepSeek
    from simulation.consumer_simulator import ConsumerSimulator
    from .behavior import BehaviorModel
    from .candidates import LocalCandidateProvider
    from .restaurants import RestaurantCandidateProvider
    from .storage import EventStore

    resolve = lambda value: Path(value) if Path(value).is_absolute() else root / value
    profile = config["profile"]
    if profile.get("consent") is not True:
        raise PermissionError("配置必须包含明确的数据使用授权")
    if user_model is None or assistant_model is None:
        key_name = config.get("llm", {}).get("api_key_env", "DEEPSEEK_API_KEY")
        key = os.environ.get(key_name)
        key_file = config.get("llm", {}).get("api_key_file")
        if not key and key_file:
            credentials = json.loads(resolve(key_file).read_text(encoding="utf-8-sig"))
            key = credentials.get("deepseek_api_key")
        if isinstance(key, str):
            key = key.strip()
        if not key:
            raise ValueError(f"请在本地密钥文件的 deepseek_api_key 中填入密钥，或设置 {key_name} 环境变量")
        llm_name = config.get("llm", {}).get("model", "deepseek-flash")
        options = {"model": llm_name, "api_key": key, "max_retries": config.get("llm", {}).get("max_retries", 2),
            "raise_on_error": True, "timeout": config.get("llm", {}).get("timeout", 45),
            "thinking": config.get("llm", {}).get("thinking", "disabled"),
            "max_tokens": config.get("llm", {}).get("max_tokens", 2048)}
        user_model = user_model or DeepSeek(**options)
        assistant_model = assistant_model or DeepSeek(**options)
    if behavior_model is None:
        model_path = config.get("behavior_model_path")
        if model_path:
            behavior_model = BehaviorModel.load(resolve(model_path))
    retriever_config = dict(config["retriever"])
    provider = retriever_config.pop("provider", "local")
    embedding_key_env = retriever_config.pop("api_key_env", "ZHIPU_API_KEY")
    embedding_key_file = retriever_config.pop("api_key_file", "config/secrets.local.json")
    embedding_key_field = retriever_config.pop("api_key_field", "siliconflow_api_key" if provider == "siliconflow" else "zhipu_api_key")
    if provider in ("zhipu", "siliconflow") and retriever_factory is None:
        from tools.api_embedding_func import ZhipuEmbeddingFunction, SiliconFlowEmbeddingFunction
        from tools.dense_retriever import DenseRetriever
        embedding_key = os.environ.get(embedding_key_env)
        if not embedding_key:
            embedding_key = json.loads(resolve(embedding_key_file).read_text(encoding="utf-8-sig")).get(embedding_key_field)
        if isinstance(embedding_key, str):
            embedding_key = embedding_key.strip()
        cls = SiliconFlowEmbeddingFunction if provider == "siliconflow" else ZhipuEmbeddingFunction
        embedder = cls(embedding_key, model=retriever_config["model_name"],
            dimensions=retriever_config["embedding_dim"])
        retriever_factory = lambda settings: DenseRetriever(**settings, embedding_function=embedder)
    elif provider == "local_text" and retriever_factory is None:
        from tools.text_retriever import TextRetriever
        retriever_factory = lambda settings: TextRetriever(**settings)
    elif provider not in ("local", "zhipu", "siliconflow", "local_text"):
        raise ValueError("不支持的嵌入服务来源")
    retriever_config["persist_directory"] = str(resolve(retriever_config.get("persist_directory", "runtime/lifesim_chroma")))
    # Chroma embeddings are the upstream retriever. Fixture injection is only for tests.
    memories = []
    for role in ("user", "assistant"):
        scope = hashlib.sha256(config["memory_scope"].encode()).hexdigest()[:24] if config.get("memory_scope") else uuid4().hex
        initial = dict(retriever_config, collection_name=f"consumer_init_{role}_{scope}")
        retriever = retriever_factory(initial) if retriever_factory else None
        memories.append(KVMemory(initial, retriever=retriever))
    dimensions_path = resolve(config.get("preference_dimensions_path", "LifeSim/upstream-lifesim/data/language_templates.json"))
    dimensions = json.loads(dimensions_path.read_text(encoding="utf-8"))
    user_agent = ConsumerUserAgent(user_model, retriever_config, logger_silent=True, dynamic_memory=memories[0])
    assistant_agent = ConsumerAssistantAgent(assistant_model, dimensions, logger_silent=True, dynamic_memory=memories[1])
    # Original event engine accepts local JSONL, then original custom-profile initialization overrides its sequence.
    events_path = resolve(config["events_path"])
    engine = OfflineLifeEventEngine(str(events_path))
    candidate_path = resolve(config["candidates_path"])
    candidate_provider = RestaurantCandidateProvider if config.get("candidate_type") == "restaurant" else LocalCandidateProvider
    simulator = ConsumerSimulator(user_agent=user_agent, assistant_agent=assistant_agent, life_event_engine=engine,
        provider=candidate_provider.from_json(candidate_path), behavior_model=behavior_model,
        store=store or EventStore(resolve(config.get("store_path", "runtime/lifesim_consumer.sqlite3"))),
        retriever_config=retriever_config, retriever_factory=retriever_factory,
        max_retries=config.get("max_retries", 2), top_k=config.get("top_k", 3))
    sequences = [json.loads(line) for line in events_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    sequence_id = config.get("sequence_id")
    sequence = next((item for item in sequences if sequence_id is None or item["id"] == sequence_id), None)
    if sequence is None or not isinstance(sequence.get("events"), list):
        raise ValueError("未找到有效的 LifeSim 事件序列")
    events = sequence["events"]
    simulator.init_env_by_custom_profile_and_events(profile, events)
    return simulator
