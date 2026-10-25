"""Actual LifeSim classes with explicit external-service fixtures; not empirical model results."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "LifeSim/upstream-lifesim/src"), str(ROOT / "tests")]
from consumer_behavior.research_service import ConsumerResearchService
from consumer_behavior.research_api import ConsumerResearchAPI
from consumer_behavior.storage import EventStore
from consumer_behavior.lifesim_runtime import create_simulator
from test_lifesim_integration import FixtureRetriever
from agents.user_agent import UserAgent
from agents.assistant_agent import AssistantAgent
from agents.memory import KVMemory


class ResearchLLM:
    model = "explicit-research-test-fixture"

    def __init__(self):
        self.calls = []
        self.invalid_choice = False
        self.invalid_quote = False

    def chat(self, messages):
        self.calls.append(copy.deepcopy(messages))
        text = messages[-1]["content"]
        try:
            p = json.loads(text)
        except (ValueError, TypeError):
            p = {}
        if "facts" in p.get("output", {}):
            return json.dumps({"facts": [
                {"field": "budget", "value": 50, "scope": "profile", "quote": "不存在的原话" if self.invalid_quote else "预算50"},
                {"field": "cuisines", "value": ["面食"], "scope": "profile", "quote": "喜欢面食"}], "notes": []}, ensure_ascii=False)
        if "choice_id" in p.get("output", {}):
            allowed = p["evidence"]["allowed_ids"]
            if self.invalid_choice:
                return json.dumps({"choice_id": "demo-japan", "ranking": ["demo-japan"], "reason": "违规"})
            choice = "demo-noodle" if "demo-noodle" in allowed else allowed[0] if allowed else "none"
            return json.dumps({"choice_id": choice, "ranking": [choice] if choice != "none" else [], "reason": "依据已确认画像和当前情境的测试模拟。"}, ensure_ascii=False)
        if "infer the user’s intention" in text:
            return "了解预算与面食偏好，保持未知项待确认。"
        return "我记下了你的表达，请在右侧确认或纠正画像信息。"


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.store = EventStore()
        self.addCleanup(self.store.close)
        self.llm = ResearchLLM()
        self.config = json.loads((ROOT / "config/consumer_lifesim.example.json").read_text(encoding="utf8"))
        self.factory = lambda config, store: create_simulator(config, user_model=self.llm, assistant_model=self.llm,
            retriever_factory=FixtureRetriever, store=store)
        self.service = ConsumerResearchService(self.config, self.store, simulator_factory=self.factory)
        self.api = ConsumerResearchAPI(self.service)
        self.session = self.service.create_session(consent=True)
        self.sid = self.session["id"]

    def ready(self):
        self.service.confirm_profile(self.sid, updates={"budget": 50, "cuisines": ["面食"], "excluded": []})

    def test_native_multiturn_history_and_pending_facts(self):
        one = self.service.chat(self.sid, "通常预算50，喜欢面食", request_id="chat-one-1234")
        self.assertIsNone(one["profile"]["budget"])
        self.assertEqual(len(one["pending_facts"]), 2)
        runtime = self.service.live[self.sid]
        self.assertIsInstance(runtime.user_agent, UserAgent)
        self.assertIsInstance(runtime.assistant_agent, AssistantAgent)
        self.assertIsInstance(runtime.assistant_agent.dynamic_memory, KVMemory)
        two = self.service.confirm_profile(self.sid, proposal_ids=[p["id"] for p in one["pending_facts"]])
        self.assertEqual(two["profile"]["budget"], 50)
        three = self.service.chat(self.sid, "通常预算50，喜欢面食，今天想少走路", request_id="chat-two-1234")
        self.assertTrue(three["messages"][-1]["retrieved_memories"])
        self.assertEqual(len(runtime.assistant_agent.intents), 2)
        self.assertGreater(len(runtime.assistant_agent.messages), 3)
        self.assertIn("通常预算50", runtime.assistant_agent.conversations.get())

    def test_chat_reports_progress_and_complete_incremental_reply(self):
        class IncrementalResearchLLM(ResearchLLM):
            def chat(self, messages, on_delta=None):
                result = super().chat(messages)
                if on_delta:
                    on_delta(result[:8])
                    on_delta(result[8:])
                return result

        self.llm = IncrementalResearchLLM()
        events = []
        result = self.service.chat(self.sid, "预算50，喜欢面食", request_id="stream-chat-1234",
            progress=lambda value: events.append(("phase", value)),
            on_delta=lambda value: events.append(("delta", value)))
        reply = result["messages"][-1]["text"]
        self.assertEqual("".join(value for kind, value in events if kind == "delta"), reply)
        self.assertEqual(events[0][0], "phase")
        self.assertEqual(result["messages"][-1]["engine_source"], "lifesim_deepseek")

    def test_opt_in_real_places_reach_chat_without_persisting_location(self):
        class Places:
            def search(self, **kwargs):
                self.last_request = kwargs
                return {"source":"amap_poi", "category":"restaurant", "items":[
                    {"id":"B001", "name":"街角面馆", "address":"和平路1号",
                     "distance_meters":350, "type":"餐饮服务;中餐厅", "average_cost_cny":42,
                     "opening_status":"unknown", "ingredients_verified":False},
                    {"id":"B002", "name":"高价餐厅", "address":"和平路2号",
                     "distance_meters":100, "type":"餐饮服务;中餐厅", "average_cost_cny":92,
                     "opening_status":"unknown", "ingredients_verified":False}]}

        self.service.nearby_provider = Places()
        self.ready()
        nearby = self.service.nearby(longitude=116.39, latitude=39.89)
        self.assertEqual(len(nearby["search_id"]), 36)
        self.assertNotIn("longitude", self.service.nearby_searches[nearby["search_id"]])
        result = self.service.chat(self.sid, "请推荐附近真实餐厅", request_id="real-place-chat-1234",
            nearby_search_id=nearby["search_id"])
        prompts = [call[0]["content"] for call in self.llm.calls if call and call[0]["role"] == "system"]
        self.assertTrue(any("街角面馆" in prompt and "和平路1号" in prompt for prompt in prompts))
        self.assertFalse(any("高价餐厅" in prompt for prompt in prompts))
        self.assertEqual(result["messages"][-1]["place_source"], "amap_poi")
        self.assertEqual(result["messages"][-1]["place_candidate_ids"], ["B001"])
        saved = self.store.get_research_session(self.sid)
        self.assertNotIn("116.39", json.dumps(saved, ensure_ascii=False))
        self.assertNotIn("和平路1号", saved["native_messages"][0]["content"])
        with self.assertRaisesRegex(ValueError, "重新点击定位"):
            self.service.chat(self.sid, "再推荐一次", request_id="expired-place-chat-1234",
                nearby_search_id="missing-search-id")

    def test_missing_quote_cannot_become_fact(self):
        self.llm.invalid_quote = True
        one = self.service.chat(self.sid, "预算50，喜欢面食", request_id="bad-quote-1234")
        self.assertEqual([p["field"] for p in one["pending_facts"]], ["cuisines"])
        self.assertIsNone(one["profile"]["budget"])

    def test_run_is_sealed_on_every_public_route(self):
        self.ready()
        data = self.service.rehearse(self.sid, request_id="run-first-1234")
        run = data["runs"][0]
        self.assertTrue(run["prediction_hidden"])
        for result in (data, self.service.get_session(self.sid), self.service.export(self.sid)["session"]):
            r = result["runs"][0]
            self.assertNotIn("prediction", r)
            self.assertNotIn("simulation_dialogue", r)
            self.assertNotIn("private_snapshot", r)
        private = self.store.get_research_session(self.sid)["runs"][0]
        self.assertEqual(private["prediction"]["choice_id"], "demo-noodle")
        self.assertEqual(len(private["simulation_dialogue"]), 4)
        self.assertIn("LifeSim.UserAgent.respond", private["native_components"])

    def test_feedback_reveals_prediction_and_remembers_actual_choice(self):
        self.ready()
        run = self.service.rehearse(self.sid, request_id="run-first-1234")["runs"][0]
        data = self.service.feedback(self.sid, run["id"], selected_id="demo-home", satisfaction=4, comment="距离和口味都重要")
        r = data["runs"][0]
        self.assertFalse(r["prediction_hidden"])
        self.assertEqual(r["observed_choice"]["id"], "demo-home")
        self.assertEqual(r["prediction"]["choice_id"], "demo-noodle")
        self.assertEqual(r["observed_choice"]["label_type"], "scenario_intention")
        self.assertLessEqual(r["prediction_frozen_at"], r["observed_choice"]["at"])
        self.assertTrue(any(m["source"] == "real_scenario_feedback" for m in self.store.get_research_session(self.sid)["memories"]))

    def test_branch_keeps_baseline_memory_and_profile_before_feedback(self):
        self.ready()
        baseline = self.service.rehearse(self.sid, request_id="run-first-1234")["runs"][0]
        self.service.feedback(self.sid, baseline["id"], selected_id="demo-home")
        self.service.confirm_profile(self.sid, updates={"budget": 100})
        data = self.service.rehearse(self.sid, branch_type="budget", request_id="run-branch-1234")
        a, b = self.store.get_research_session(self.sid)["runs"]
        self.assertEqual(b["profile_snapshot"], a["profile_snapshot"])
        self.assertEqual(b["profile_snapshot"]["budget"], 50)
        self.assertEqual(b["context_snapshot"]["budget"], 30)
        self.assertEqual(b["private_snapshot"]["memories"], a["private_snapshot"]["memories"])
        self.assertEqual(b["prediction"]["choice_id"], "none")
        self.assertEqual(data["profile"]["budget"], 100)

    def test_hallucinated_or_overbudget_choice_rejected(self):
        self.ready()
        self.llm.invalid_choice = True
        with self.assertRaises(ValueError):
            self.service.rehearse(self.sid, request_id="bad-run-1234")
        self.assertEqual(self.service.get_session(self.sid)["runs"], [])

    def test_idempotence_and_session_ownership(self):
        self.ready()
        first = self.service.rehearse(self.sid, request_id="run-first-1234")
        calls = len(self.llm.calls)
        second = self.service.rehearse(self.sid, request_id="run-first-1234")
        self.assertEqual(len(second["runs"]), 1)
        self.assertEqual(calls, len(self.llm.calls))
        other = self.service.create_session(consent=True)["id"]
        with self.assertRaises(KeyError):
            self.service.feedback(other, first["runs"][0]["id"], selected_id="none")
        self.assertEqual(self.service.get_session(other)["profile"]["budget"], None)

    def test_restart_rehydrates_native_memory(self):
        self.ready()
        restarted = ConsumerResearchService(self.config, self.store, simulator_factory=self.factory)
        reply = restarted.chat(self.sid, "预算50，喜欢面食", request_id="new-chat-1234")
        runtime = restarted.live[self.sid]
        self.assertIn("50", runtime.user_agent.get_profile())
        self.assertTrue(runtime.assistant_agent.dynamic_memory.search("预算"))
        self.assertEqual(reply["profile"]["budget"], 50)

    def test_unknown_missing_budget_and_skip_not_negative_label(self):
        data = self.service.rehearse(self.sid, request_id="no-budget-1234")
        self.assertEqual(data["runs"], [])
        self.ready()
        run = self.service.rehearse(self.sid, request_id="run-first-1234")["runs"][0]
        data = self.service.feedback(self.sid, run["id"], skip=True)
        self.assertIsNone(data["runs"][0]["observed_choice"])
        self.assertTrue(data["runs"][0]["skipped"])

    def test_delete_session_removes_durable_profile(self):
        self.ready()
        self.service.chat(self.sid, "预算50，喜欢面食", request_id="chat-one-1234")
        self.service.delete(self.sid)
        with self.assertRaises(KeyError):
            self.service.get_session(self.sid)
        with self.assertRaises(KeyError):
            self.store.get_profile(self.sid)


if __name__ == "__main__":
    unittest.main()
