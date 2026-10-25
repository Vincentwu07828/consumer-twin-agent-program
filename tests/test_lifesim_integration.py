"""Tests call real upstream LifeSim classes. Only the external LLM/embedding service is a fixture."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "LifeSim/upstream-lifesim/src")]

from agents.user_agent import UserAgent
from agents.assistant_agent import AssistantAgent
from agents.memory import SimpleMemory, KVMemory
from profiles.profile_generator import UserProfile
from simulation.conversation_simulator import ConversationSimulator
from consumer_behavior import ConstantClickModel, EventStore
from consumer_behavior.lifesim_runtime import create_simulator


class FixtureRetriever:
    """Explicit test fixture injected into original KVMemory, not a production fallback."""
    def __init__(self, config):
        self.config = config
        self.items = []

    def reset_collection(self):
        self.items = []

    def build_index(self, data):
        self.items = copy.deepcopy(data)

    def add_documents(self, data):
        self.items.extend(copy.deepcopy(data))

    def search(self, query, top_k=3, return_scores=True):
        return [{"data": item, "score": .9} for item in reversed(self.items[-top_k:])]


class FixtureLLM:
    model = "test-fixture-not-deepseek"

    def __init__(self, bad_plans=0):
        self.calls = []
        self.bad_plans = bad_plans

    def chat(self, messages):
        self.calls.append(copy.deepcopy(messages))
        content = messages[-1]["content"]
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if payload and "recommended_ids" in payload.get("output", {}):
            if self.bad_plans:
                self.bad_plans -= 1
                return json.dumps({"recommended_ids": ["demo-premium"], "reasons": {"demo-premium": "预算外"}})
            preferred = "demo-tea" if "明确喜欢茉莉茶" in payload["profile"] else "demo-coffee"
            selected = [preferred] if preferred in payload["evidence"]["allowed_ids"] else []
            return json.dumps({"recommended_ids": selected, "reasons": {cid: "结合明确偏好与候选条件" for cid in selected}}, ensure_ascii=False)
        if payload and "explanation" in payload.get("output", {}):
            return json.dumps({"explanation": "这是已采样点击的测试解释，不是真实动机。"}, ensure_ascii=False)
        if "likely preferences (high/middle/low)" in content:
            return json.dumps([{"dim": "Preference for a certain food", "value": "high"}])
        if "infer the user’s intention" in content:
            return "用户希望获得符合预算和偏好的饮品建议。"
        return "我想选一杯饮品，请参考我的明确偏好。"


class LifeSimIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.store = EventStore()
        self.addCleanup(self.store.close)
        self.model = FixtureLLM()
        self.config = json.loads((ROOT / "config/consumer_lifesim.example.json").read_text(encoding="utf-8"))
        self.config.pop("candidate_type", None)
        self.config["candidates_path"] = "data/examples/candidates.json"
        self.config["profile"].update(consent=True, preferences=["明确喜欢茉莉茶"],
            preferences_value={"Preference for a certain food": "high"})
        # Two events allow an observed feedback / retrieval cycle through the original engine.
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.addCleanup(self.temp.cleanup)
        sequence = json.loads((ROOT / "data/examples/consumer_events.jsonl").read_text(encoding="utf-8"))
        event = sequence["events"][0]
        sequence["events"] = [copy.deepcopy(event) for _ in range(4)]
        path = Path(self.temp.name) / "events.jsonl"
        path.write_text(json.dumps(sequence, ensure_ascii=False), encoding="utf-8")
        self.config["events_path"] = str(path)

    def make_simulator(self, probability=.5, **overrides):
        model = overrides.get("model", self.model)
        return create_simulator(self.config, user_model=model, assistant_model=model,
            retriever_factory=FixtureRetriever, behavior_model=ConstantClickModel(probability, version="test-fixture-only"),
            store=self.store)

    def test_actual_upstream_personalization_classes_are_used(self):
        sim = self.make_simulator()
        self.assertIsInstance(sim, ConversationSimulator)
        self.assertIsInstance(sim.user_profile, UserProfile)
        self.assertIsInstance(sim.user_agent, UserAgent)
        self.assertIsInstance(sim.assistant_agent, AssistantAgent)
        self.assertIsInstance(sim.user_agent.static_memory, SimpleMemory)
        self.assertIsInstance(sim.user_agent.dynamic_memory, KVMemory)
        self.assertIsInstance(sim.assistant_agent.dynamic_memory, KVMemory)
        self.assertIs(sim.user_agent.__class__.respond, UserAgent.respond)
        self.assertIs(sim.assistant_agent.__class__.respond, AssistantAgent.respond)

    def test_assistant_personalization_flows_into_llm_recommendation(self):
        sim = self.make_simulator()
        trace = sim.run_consumer_episode(mode="assistant", user_input="30元内买饮品")
        self.assertEqual(trace["decision"]["exposed_ids"], ["demo-tea"])
        self.assertEqual(trace["decision"]["clicked_ids"], [])
        self.assertEqual(trace["response_source"], "llm_inference")
        self.assertEqual(trace["profile_component"], "LifeSim.UserProfile")
        self.assertGreaterEqual(len(self.model.calls), 3)  # original intent, response, added structured planner
        sim.user_agent._build_profile("明确喜欢咖啡")
        trace2 = sim.run_consumer_episode(mode="assistant", user_input="30元内买饮品")
        self.assertEqual(trace2["decision"]["exposed_ids"], ["demo-coffee"])

    def test_real_feedback_enters_original_kv_memory_and_next_episode(self):
        sim = self.make_simulator()
        trace = sim.run_consumer_episode(mode="assistant", user_input="想喝茶")
        sim.record_consumer_feedback(trace["run_id"], clicked={"demo-tea": True}, satisfaction=5, comment="喜欢这款茶")
        matches = sim.user_agent.dynamic_memory.search("喜欢茶")
        self.assertIn("喜欢这款茶", matches[0][0]["response"])
        self.assertTrue(sim.assistant_agent.user_preferences)
        self.assertEqual(sim.user_profile.extra["llm_inferred_preferences"]["source"], "llm_inference")
        trace2 = sim.run_consumer_episode(mode="assistant", user_input="再选饮品")
        self.assertTrue(trace2["memories"])
        self.assertEqual(trace2["memories"][0]["retriever"], "LifeSim.KVMemory")
        self.assertEqual(trace2["memories"][0]["source"], "real")

    def test_feedback_and_memory_survive_new_simulator_instance(self):
        sim = self.make_simulator()
        first = sim.run_consumer_episode(mode="assistant", user_input="想喝茶")
        sim.record_consumer_feedback(first["run_id"], clicked={"demo-tea": True})
        restarted = self.make_simulator()
        trace = restarted.run_consumer_episode(mode="assistant", user_input="继续喝茶")
        self.assertTrue(trace["memories"])

    def test_simulation_calls_original_user_llm_and_keeps_feedback_separate(self):
        sim = self.make_simulator(probability=1)
        trace = sim.run_consumer_episode(mode="simulation", seed=42)
        self.assertEqual(trace["user_utterance_source"], "lifesim_user_llm_simulation")
        self.assertEqual(set(trace["decision"]["clicked_ids"]), {"demo-tea", "demo-coffee"})
        self.assertEqual(trace["state_after"]["budget_cny"], 30)
        self.assertEqual(self.store.memories(sim.user_profile.user_id, "real"), [])
        self.assertEqual(len(self.store.memories(sim.user_profile.user_id, "simulated")), 2)
        next_trace = sim.run_consumer_episode(mode="simulation", seed=42)
        self.assertEqual(len(next_trace["state_after"]["simulated_clicks"]), 4)
        assistant_trace = sim.run_consumer_episode(mode="assistant", user_input="现在真实选饮品")
        self.assertEqual(assistant_trace["memories"], [])
        self.assertNotIn("已采样点击", assistant_trace["conversations"]["assistant"])

    def test_llm_cannot_override_constraints_retries_then_succeeds(self):
        model = FixtureLLM(bad_plans=1)
        sim = self.make_simulator(model=model)
        trace = sim.run_consumer_episode(mode="assistant", user_input="不超过30元")
        self.assertEqual(len(trace["attempts"]), 2)
        self.assertIn("planner_error", trace["attempts"][0])
        self.assertEqual(trace["decision"]["exposed_ids"], ["demo-tea"])

    def test_invalid_llm_output_fails_at_retry_limit_and_is_recorded(self):
        sim = self.make_simulator(model=FixtureLLM(bad_plans=10))
        with self.assertRaises(ValueError):
            sim.run_consumer_episode(mode="assistant", user_input="30元内")
        trace = json.loads(self.store.connection.execute("SELECT payload FROM traces").fetchone()[0])
        self.assertEqual(trace["status"], "failed")
        self.assertEqual(len(trace["attempts"]), 3)

    def test_unknown_attributes_not_guessed_from_lifesim_text(self):
        sim = self.make_simulator()
        sim.user_profile.age = "20"
        self.assertNotIn("age_level", sim._feature_view().attributes)
        sim.user_profile.extra["consumer_features"] = {"age_level": {"value": 3, "source": "questionnaire", "confidence": .8}}
        self.assertEqual(sim._feature_view().attributes["age_level"].value, 3)

    def test_no_consent_prevents_llm_call(self):
        sim = self.make_simulator()
        sim.user_profile.extra["consent"] = False
        with self.assertRaises(PermissionError):
            sim.run_consumer_episode(mode="assistant", user_input="需求")
        self.assertFalse(self.model.calls)

    def test_actual_deepseek_class_uses_official_endpoint_and_records_chat(self):
        from models.cloud import DeepSeek
        llm = DeepSeek(model="deepseek-flash", api_key="test-not-a-real-key", max_retries=1, raise_on_error=True)
        self.assertEqual(str(llm.client.base_url).rstrip("/"), "https://api.deepseek.com")
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="测试回复"))])
        with patch.object(llm.client.chat.completions, "create", return_value=response) as call:
            self.assertEqual(llm.chat([{"role": "user", "content": "测试"}]), "测试回复")
            self.assertEqual(call.call_args.kwargs["model"], "deepseek-flash")
        self.assertEqual(len(llm.messages), 1)
        with patch.object(llm.client.chat.completions, "create", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                llm.chat([{"role": "user", "content": "测试失败"}])

    def test_deepseek_stream_delivers_chunks_and_stores_complete_reply(self):
        from models.cloud import DeepSeek
        llm = DeepSeek(model="deepseek-flash", api_key="test-not-a-real-key", max_retries=1, raise_on_error=True)
        chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=part))])
                  for part in ("现在", "可以", "逐步显示")]
        received = []
        with patch.object(llm.client.chat.completions, "create", return_value=iter(chunks)) as call:
            result = llm.chat([{"role": "user", "content": "测试"}], on_delta=received.append)
        self.assertEqual(received, ["现在", "可以", "逐步显示"])
        self.assertEqual(result, "现在可以逐步显示")
        self.assertTrue(call.call_args.kwargs["stream"])
        self.assertEqual(llm.messages[-1][-1]["content"], result)

    def test_actual_lifesim_dense_retriever_with_chroma_and_fixture_embeddings(self):
        # Chroma holds its SQLite handle until process exit on Windows.
        # Isolate the native probe so temporary resources can be cleaned afterward.
        result = subprocess.run([sys.executable, str(ROOT / "tests/retriever_probe.py"), self.temp.name],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            env=dict(os.environ, PYTHONUTF8="1"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("native_lifesim_retriever_ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
