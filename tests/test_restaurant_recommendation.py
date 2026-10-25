import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "LifeSim/upstream-lifesim/src"), str(ROOT / "tests")]

from consumer_behavior.api import RestaurantAPI
from consumer_behavior.intent import meal_intent
from consumer_behavior.questionnaire import questionnaire_profile
from consumer_behavior.restaurants import RestaurantCandidate, RestaurantCandidateProvider, RestaurantConstraintChecker
from consumer_behavior.recommendation import RestaurantDecisionModule
from consumer_behavior import ConsumerProfile, ConsumerState, EventStore
from consumer_behavior.lifesim_runtime import create_simulator
from consumer_behavior.service import RestaurantAgentService
from test_lifesim_integration import FixtureRetriever


def restaurants():
    return [RestaurantCandidate("r-sichuan", "测试川菜餐厅", price_cny=40, cuisines=["川菜"], is_open=True,
            distance_meters=500, ingredients=["牛肉"], allergens=[], allergen_info_verified=True,
            diet_tags=[], source="test_fixture"),
        RestaurantCandidate("r-noodle", "测试面馆", price_cny=20, cuisines=["面食"], is_open=True,
            distance_meters=300, ingredients=["面粉"], allergens=["小麦"], allergen_info_verified=True,
            diet_tags=["素食"], source="test_fixture"),
        RestaurantCandidate("r-expensive", "测试高价餐厅", price_cny=100, cuisines=["川菜"], is_open=True,
            distance_meters=100, ingredients=[], allergens=[], allergen_info_verified=True, source="test_fixture")]


class RestaurantLLM:
    model = "restaurant-test-fixture"
    def __init__(self):
        self.calls = []
    def chat(self, messages):
        self.calls.append(copy.deepcopy(messages))
        text = messages[-1]["content"]
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return "用户想要一顿符合预算和口味的晚餐。"
        if "recommended_ids" in payload.get("output", {}):
            self.assert_no_probability(payload)
            allowed = payload["evidence"]["allowed_ids"]
            prefer = "r-sichuan" if "川菜" in payload["profile"] else "r-noodle"
            selected = ([prefer] if prefer in allowed else []) + [cid for cid in allowed if cid != prefer]
            return json.dumps({"recommended_ids": selected[:3],
                "reasons": {cid: "结合明确口味、预算和历史反馈" for cid in selected[:3]}}, ensure_ascii=False)
        return ""
    @staticmethod
    def assert_no_probability(payload):
        assert "scores" not in payload["evidence"]
        assert "optional_behavior_scores" not in payload["evidence"]


class RestaurantRuleTests(unittest.TestCase):
    def test_hard_rules_and_soft_cuisine_preferences(self):
        state = ConsumerState(budget_cny=50, max_distance_meters=1000, preferred_cuisines=["川菜"])
        checker = RestaurantConstraintChecker()
        self.assertTrue(checker.check(restaurants()[1], state).feasible)
        self.assertIn("over_budget", checker.check(restaurants()[2], state).reasons)
        closed = RestaurantCandidate("closed", "闭店", price_cny=30, is_open=False, source="test_fixture")
        self.assertIn("restaurant_closed", checker.check(closed, state).reasons)
        far = RestaurantCandidate("far", "太远", price_cny=30, distance_meters=2000, source="test_fixture")
        self.assertIn("too_far", checker.check(far, state).reasons)

    def test_unknown_allergen_information_not_treated_as_safe(self):
        checker = RestaurantConstraintChecker()
        state = ConsumerState(allergies=["小麦"])
        self.assertIn("allergen_conflict", checker.check(restaurants()[1], state).reasons)
        unknown = RestaurantCandidate("unknown", "未知食材", allergens=[], source="test_fixture")
        self.assertIn("allergen_information_unverified", checker.check(unknown, state).reasons)
        self.assertTrue(checker.check(restaurants()[0], state).feasible)

    def test_diet_and_ingredient_unknowns_are_explicit(self):
        checker = RestaurantConstraintChecker()
        state = ConsumerState(excluded_ingredients=["牛肉"])
        self.assertIn("excluded_ingredient", checker.check(restaurants()[0], state).reasons)
        vegetarian = ConsumerState(required_diet_tags=["素食"])
        self.assertTrue(checker.check(restaurants()[1], vegetarian).feasible)
        self.assertIn("diet_requirement_not_met", checker.check(restaurants()[0], vegetarian).reasons)

    def test_llm_receives_only_feasible_restaurants_and_can_rank_by_preference(self):
        def planner(evidence):
            self.assertEqual(set(evidence["allowed_ids"]), {"r-sichuan", "r-noodle"})
            self.assertNotIn("scores", evidence)
            return {"recommended_ids": ["r-sichuan", "r-noodle"], "reasons": {"r-sichuan": "符合口味", "r-noodle": "预算余量更大"}}
        result = RestaurantDecisionModule().recommend(profile=ConsumerProfile("u"),
            state=ConsumerState(budget_cny=50), provider=RestaurantCandidateProvider(restaurants()),
            memories=[], need="晚餐", planner=planner, attempts=[])
        self.assertEqual(result.decision.exposed_ids[0], "r-sichuan")
        self.assertEqual(result.scores, [])

    def test_invalid_llm_candidate_retried_without_relaxing_constraints(self):
        calls = []
        def planner(evidence):
            calls.append(evidence)
            if len(calls) == 1:
                return {"recommended_ids": ["r-expensive"], "reasons": {"r-expensive": "违规"}}
            self.assertTrue(evidence["previous_validation_error"])
            return {"recommended_ids": ["r-noodle"], "reasons": {"r-noodle": "符合条件"}}
        result = RestaurantDecisionModule().recommend(profile=ConsumerProfile("u"),
            state=ConsumerState(budget_cny=50), provider=RestaurantCandidateProvider(restaurants()),
            memories=[], need="晚餐", planner=planner, attempts=[])
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.decision.exposed_ids, ["r-noodle"])

    def test_no_candidates_does_not_call_llm(self):
        result = RestaurantDecisionModule().recommend(profile=ConsumerProfile("u"),
            state=ConsumerState(budget_cny=1), provider=RestaurantCandidateProvider(restaurants()),
            memories=[], need="晚餐", planner=lambda _: self.fail("不应调用 LLM"), attempts=[])
        self.assertEqual(result.decision.action, "no_candidates")

    def test_questionnaire_preserves_unknown_attributes(self):
        profile = questionnaire_profile("u", {"preferred_cuisines": ["川菜"], "meal_budget_cny": 50}, consent=True)
        self.assertIsNone(profile["income"])
        self.assertIsNone(profile["age"])
        self.assertNotIn("allergies", profile["consumer_preferences"])
        self.assertEqual(profile["attribute_provenance"]["meal_budget_cny"]["source"], "user_questionnaire")
        with self.assertRaises(ValueError):
            questionnaire_profile("u", {"meal_budget_cny": float("nan")}, consent=True)

    def test_six_pm_suggests_confirmation_not_known_hunger(self):
        result = meal_intent(now="2026-10-14T18:00:00+08:00")
        self.assertEqual(result["intent"], "possible_dining")
        self.assertFalse(result["confirmed"])
        self.assertTrue(result["requires_confirmation"])
        recent = meal_intent(now="2026-10-14T18:00:00+08:00", last_meal_at="2026-10-14T17:30:00+08:00")
        self.assertEqual(recent["intent"], "none")
        denied = meal_intent(now="2026-10-14T18:00:00+08:00", explicit_hunger=False)
        self.assertEqual(denied["intent"], "none")


class RestaurantServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = EventStore()
        self.addCleanup(self.store.close)
        self.llm = RestaurantLLM()
        self.config = json.loads((ROOT / "config/consumer_lifesim.example.json").read_text(encoding="utf-8"))
        def factory(config, store):
            return create_simulator(config, store=store, user_model=self.llm, assistant_model=self.llm,
                                    retriever_factory=FixtureRetriever)
        self.service = RestaurantAgentService(self.config, self.store, simulator_factory=factory)
        self.api = RestaurantAPI(self.service)
        created = self.api.dispatch("POST", "/api/sessions", {"consent": True,
            "answers": {"preferred_cuisines": ["川菜"], "meal_budget_cny": 50, "allergies": ["小麦"]}})
        self.session = created["session_id"]

    def recommend(self, **kwargs):
        payload = {"session_id": self.session, "need": "帮我选晚餐", "candidates": [r.to_dict() for r in restaurants()]}
        payload.update(kwargs)
        return self.api.dispatch("POST", "/api/recommendations", payload)

    def test_complete_questionnaire_recommendation_feedback_memory_cycle_without_model(self):
        result = self.recommend()
        self.assertFalse(result["behavior_model_required"])
        self.assertEqual(result["recommendations"][0]["restaurant"]["candidate_id"], "r-sichuan")
        reasons = {r["candidate_id"]: r["reasons"] for r in result["filtered_out"]}
        self.assertIn("allergen_conflict", reasons["r-noodle"])
        self.assertIn("over_budget", reasons["r-expensive"])
        trace = self.store.get_trace(result["run_id"], self.session)
        self.assertFalse(trace["behavior_model_enabled"])
        self.assertEqual(trace["scores"], [])
        self.assertEqual(trace["state_before"]["budget_cny"], 50)
        self.assertEqual(trace["profile_component"], "LifeSim.UserProfile")
        feedback = self.api.dispatch("POST", "/api/feedback", {"session_id": self.session,
            "run_id": result["run_id"], "selected_id": "r-sichuan", "satisfaction": 5, "comment": "口味很合适"})
        self.assertEqual(feedback["status"], "feedback_recorded")
        remembered = self.recommend()
        memories = self.store.get_trace(remembered["run_id"], self.session)["memories"]
        self.assertTrue(any("测试川菜餐厅" in m["text"] and "口味很合适" in m["text"] for m in memories))
        self.assertFalse(any("clicked" in m for m in self.store.memories(self.session)))

    def test_request_cannot_clear_confirmed_allergies(self):
        result = self.recommend(context={"allergies": []})
        self.assertEqual([r["restaurant"]["candidate_id"] for r in result["recommendations"]], ["r-sichuan"])

    def test_clock_trigger_waits_for_confirmation_without_llm(self):
        result = self.recommend(need="", context={"time": "2026-10-14T18:00:00+08:00"})
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertFalse(self.llm.calls)
        confirmed = self.recommend(need="", explicit_hunger=True)
        self.assertEqual(confirmed["status"], "recommend")

    def test_invalid_feedback_rejected_and_sessions_isolated(self):
        result = self.recommend()
        with self.assertRaises(ValueError):
            self.api.dispatch("POST", "/api/feedback", {"session_id": self.session,
                "run_id": result["run_id"], "selected_id": "r-noodle"})
        second = self.service.create_session({}, True)["session_id"]
        with self.assertRaises(KeyError):
            self.api.dispatch("POST", "/api/feedback", {"session_id": second,
                "run_id": result["run_id"], "selected_id": "r-sichuan"})

    def test_explicit_rejection_reason_is_remembered_without_negative_click_label(self):
        self.service.simulators.clear()
        profile = self.store.get_profile(self.session)
        profile["consumer_preferences"]["allergies"] = []
        self.store.save_profile(profile)
        result = self.recommend()
        self.api.dispatch("POST", "/api/feedback", {"session_id": self.session, "run_id": result["run_id"],
            "selected_id": "r-sichuan", "rejected_reasons": {"r-noodle": "今天不想吃面"}})
        memories = self.store.memories(self.session)
        self.assertIn("今天不想吃面", memories[0]["text"])
        self.assertNotIn("clicked", memories[0])

    def test_clear_session_removes_personal_records(self):
        self.recommend()
        self.api.dispatch("DELETE", "/api/session", {"session_id": self.session})
        with self.assertRaises(KeyError):
            self.service.profile(self.session)
        self.assertEqual(self.store.memories(self.session), [])

    def test_questionnaire_and_health_need_no_api_credentials(self):
        self.assertFalse(self.api.dispatch("GET", "/api/health")["training_required"])
        self.assertTrue(self.api.dispatch("GET", "/api/questionnaire")["questions"])

    def test_missing_budget_is_requested_before_llm_instead_of_invented(self):
        session_id = self.service.create_session({}, True)["session_id"]
        result = self.service.recommend(session_id, need="推荐晚餐", candidates=[r.to_dict() for r in restaurants()])
        self.assertEqual(result["status"], "needs_budget")
        self.assertFalse(self.llm.calls)


if __name__ == "__main__":
    unittest.main()
