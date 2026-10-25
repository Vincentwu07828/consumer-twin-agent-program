import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from consumer_behavior import (ConsumerProfile, ProfileAttribute, ConsumerState, Candidate,
    LocalCandidateProvider, ConstantClickModel, DecisionPolicy, Mode, Harness,
    MemoryStore, EventStore, FeedbackRecorder)
from consumer_behavior.behavior import CandidateScore
from consumer_behavior.constraints import ConstraintChecker
from consumer_behavior.features import FeatureAdapter, SPACE, PRICE_ENCODING


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.store = EventStore()
        self.addCleanup(self.store.close)
        self.profile = ConsumerProfile("u1", consent=True)
        self.state = ConsumerState(budget_cny=30, need="tea")
        self.items = [Candidate("tea", "Tea", "drinks", price_cny=18, in_stock=True),
                      Candidate("coffee", "Coffee", "drinks", price_cny=28, in_stock=True),
                      Candidate("expensive", "Gift", "drinks", price_cny=128, in_stock=True),
                      Candidate("empty", "Juice", "drinks", price_cny=12, in_stock=False),
                      Candidate("unknown", "Unknown price", "drinks", feature_price=.01)]

    def harness(self, ctr=.5, policy=None, model=None):
        return Harness(LocalCandidateProvider(self.items), model or ConstantClickModel(ctr),
                       MemoryStore(self.store), self.store, policy=policy)

    def test_constraints_use_actual_price_not_encoded_price(self):
        trace = self.harness().run(self.profile, self.state)
        self.assertEqual(set(trace["decision"]["exposed_ids"]), {"tea", "coffee"})
        checks = {c["candidate_id"]: c for c in trace["attempts"][0]["initial_checks"]}
        self.assertIn("over_budget", checks["expensive"]["reasons"])
        self.assertIn("actual_price_unknown", checks["unknown"]["reasons"])
        self.assertEqual(self.state.budget_cny, 30)

    def test_independent_clicks_allow_zero_and_multiple(self):
        for probability, expected in ((0, []), (1, ["coffee", "tea"])):
            scores = [CandidateScore(cid, probability, "click", "test", "test") for cid in ("tea", "coffee")]
            decision = DecisionPolicy().decide(Mode.SIMULATION, scores, seed=7)
            self.assertEqual(decision.clicked_ids, expected)

    def test_seed_reproducible_under_candidate_reordering(self):
        scores = [CandidateScore(str(i), .5, "click", "test", "test") for i in range(30)]
        policy = DecisionPolicy()
        self.assertEqual(policy.decide(Mode.SIMULATION, scores, seed=42),
                         policy.decide(Mode.SIMULATION, list(reversed(scores)), seed=42))

    def test_simulation_does_not_spend_or_create_real_memory(self):
        trace = self.harness(1).run(self.profile, self.state, Mode.SIMULATION, seed=1)
        self.assertEqual(self.state.budget_cny, 30)
        self.assertEqual(set(self.state.simulated_clicks), {"tea", "coffee"})
        self.assertEqual(MemoryStore(self.store).retrieve("u1", "tea"), [])
        self.assertEqual(len(self.store.memories("u1", "simulated")), 2)
        self.assertEqual(self.store.get_trace(trace["run_id"], "u1")["state_after"], self.state.to_dict())

    def test_assistant_does_not_choose_for_user(self):
        trace = self.harness(1).run(self.profile, self.state)
        self.assertEqual(trace["decision"]["action"], "recommend")
        self.assertEqual(trace["decision"]["clicked_ids"], [])
        self.assertEqual(self.state.simulated_clicks, [])

    def test_unreported_feedback_stays_unknown(self):
        trace = self.harness().run(self.profile, self.state)
        FeedbackRecorder(self.store).record(self.profile, trace["run_id"], source="real",
                                           clicked={"tea": True}, satisfaction=4)
        memories = self.store.memories("u1")
        self.assertEqual(len(memories), 1)
        self.assertTrue(memories[0]["clicked"])
        self.assertEqual(self.profile.attributes["observed_click_count"].value, 1)
        self.assertEqual(self.profile.attributes["observed_labeled_exposure_count"].value, 1)

    def test_feedback_rejects_unexposed_candidate(self):
        trace = self.harness(policy=DecisionPolicy(top_k=1)).run(self.profile, self.state)
        exposed = trace["decision"]["exposed_ids"]
        unexposed = ({"tea", "coffee"} - set(exposed)).pop()
        with self.assertRaises(ValueError):
            FeedbackRecorder(self.store).record(self.profile, trace["run_id"], source="real",
                                               clicked={unexposed: True})
        self.assertEqual(self.store.memories("u1"), [])

    def test_feedback_cannot_relabel_simulated_as_real(self):
        trace = self.harness().run(self.profile, self.state, Mode.SIMULATION)
        with self.assertRaises(ValueError):
            FeedbackRecorder(self.store).record(self.profile, trace["run_id"], source="real", clicked={"tea": True})

    def test_feedback_cannot_count_same_exposure_twice(self):
        trace = self.harness().run(self.profile, self.state)
        recorder = FeedbackRecorder(self.store)
        recorder.record(self.profile, trace["run_id"], source="real", clicked={"tea": True})
        with self.assertRaises(ValueError):
            recorder.record(self.profile, trace["run_id"], source="real", clicked={"tea": True})
        self.assertEqual(len(self.store.memories("u1")), 1)

    def test_replay_and_clear_are_user_scoped(self):
        trace1 = self.harness().run(self.profile, self.state)
        other = ConsumerProfile("u2", consent=True)
        trace2 = self.harness().run(other, self.state)
        with self.assertRaises(KeyError):
            self.store.get_trace(trace1["run_id"], "u2")
        self.store.clear_user("u1")
        with self.assertRaises(KeyError):
            self.store.get_trace(trace1["run_id"], "u1")
        self.assertEqual(self.store.get_trace(trace2["run_id"], "u2")["user_id"], "u2")

    def test_no_consent_no_trace(self):
        profile = ConsumerProfile("no-consent")
        with self.assertRaises(PermissionError):
            self.harness().run(profile, self.state)
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM traces").fetchone()[0], 0)

    def test_no_candidates(self):
        self.state.budget_cny = 0
        trace = self.harness().run(self.profile, self.state)
        self.assertEqual(trace["decision"]["action"], "no_candidates")
        self.assertEqual(trace["scores"], [])

    def test_failure_records_trace_and_preserves_state(self):
        class BrokenModel:
            def score_candidates(self, *args):
                raise RuntimeError("model unavailable")
        before = self.state.to_dict()
        with self.assertRaises(RuntimeError):
            self.harness(model=BrokenModel()).run(self.profile, self.state, Mode.SIMULATION)
        row = self.store.connection.execute("SELECT payload FROM traces").fetchone()
        self.assertEqual(json.loads(row[0])["status"], "failed")
        self.assertEqual(self.state.to_dict(), before)

    def test_output_constraint_retry_is_bounded(self):
        class ChangingChecker:
            def __init__(self):
                self.calls = 0
            def check(self, candidate, state):
                self.calls += 1
                if self.calls % 2 == 0:
                    state.budget_cny = 0
                else:
                    state.budget_cny = 30
                return ConstraintChecker().check(candidate, state)
        harness = Harness(LocalCandidateProvider([self.items[0]]), ConstantClickModel(1),
                          MemoryStore(self.store), self.store, checker=ChangingChecker(), max_retries=1)
        with self.assertRaises(RuntimeError):
            harness.run(self.profile, self.state)
        row = self.store.connection.execute("SELECT payload FROM traces").fetchone()
        self.assertEqual(len(json.loads(row[0])["attempts"]), 2)

    def test_invalid_money_and_probability_rejected(self):
        for invalid in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ConsumerState(budget_cny=invalid)
            with self.assertRaises(ValueError):
                Candidate("x", "X", price_cny=invalid)
            with self.assertRaises(ValueError):
                ConstantClickModel(invalid)


class FeatureTests(unittest.TestCase):
    def test_online_and_training_share_features(self):
        profile = ConsumerProfile("web-local-id", {"age_level": ProfileAttribute(3, "questionnaire")},
                                  feature_space=SPACE)
        candidate = Candidate("local-product", "Ad", category_id="10", brand_id="20",
                              feature_space=SPACE, feature_price=.5, price_encoding=PRICE_ENCODING)
        history = [{"source": "real", "feature_space": SPACE, "clicked": True,
                    "category_id": "10", "brand_id": "20"}]
        adapter = FeatureAdapter()
        actual = adapter.online_rows(profile, history, [candidate])[0][0]
        expected = adapter.from_row({"age_level": 3, "cate_id": 10, "brand": 20, "price": .5,
                                     "cate_his": "10", "brand_his": "20"})[0]
        self.assertEqual(actual, expected)
        self.assertNotIn("userid", actual)

    def test_namespace_and_simulation_history_not_fabricated(self):
        profile = ConsumerProfile("u", {"age_level": ProfileAttribute(3, "llm_inference")}, feature_space=SPACE)
        candidate = Candidate("x", "Demo", category_id="999", price_cny=18)
        history = [{"source": "simulated", "feature_space": SPACE, "clicked": True,
                    "category_id": "999", "brand_id": "9"}]
        rows, missing, warnings = FeatureAdapter().online_rows(profile, history, [candidate])
        self.assertIn("age_level", missing[0])
        self.assertIn("price", missing[0])
        self.assertIn("cate_his", missing[0])
        self.assertIn("candidate_feature_space_mismatch", warnings[0])
        self.assertEqual(rows[0]["price_missing"], 1)

    def test_history_is_bounded_and_unknown_distinct_from_empty(self):
        adapter = FeatureAdapter()
        row = {"cate_his": "^".join(["1"] * 50 + ["2"] * 50), "cate_id": "2", "brand_his": ""}
        features, missing = adapter.from_row(row)
        self.assertEqual(features["cate_his_length"], 50)
        self.assertEqual(features["cate_his_match_rate"], 1)
        self.assertEqual(features["brand_his_missing"], 0)
        self.assertNotIn("brand_his", missing)
        self.assertEqual(adapter.from_row({})[0]["brand_his_missing"], 1)


if __name__ == "__main__":
    unittest.main()
