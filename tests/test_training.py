from pathlib import Path
import sys
import tempfile
import unittest
import random
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from consumer_behavior import ConsumerProfile, Candidate, BehaviorModel
from consumer_behavior.data import inspect_rows, read_rows, label
from consumer_behavior.features import FeatureSpec, FeatureAdapter, SPACE, PRICE_ENCODING
from consumer_behavior.training import train_behavior, split_training, metrics


def synthetic_rows(n, offset=0, seed=7):
    """Test fixtures only, deliberately not evidence of real consumer accuracy."""
    rng = random.Random(seed)
    result = []
    for index in range(n):
        age = rng.choice([1, 2, 3])
        category = rng.choice([10, 20])
        history = "10^10^20" if rng.random() < .5 else "20^20^20"
        probability = .8 if age == 1 and category == 10 else .12
        result.append({"userid": str(offset + index), "adgroup_id": str(index % 7),
            "age_level": age, "pvalue_level": 2, "shopping_level": 2, "final_gender_code": 1,
            "occupation": 0, "new_user_class_level": 2, "cate_id": category, "brand": index % 3,
            "price": rng.random(), "cate_his": history, "brand_his": "0^1^2",
            "clk": int(rng.random() < probability)})
    return result


class TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.train = synthetic_rows(1000)
        cls.test = synthetic_rows(200, offset=5000, seed=77)
        cls.report = train_behavior(cls.train, cls.test, cls.tmp.name, data_version="synthetic-fixture-v1",
                                    synthetic=True)
        cls.model = BehaviorModel.load(Path(cls.tmp.name) / "behavior.joblib")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_fit_calibration_and_metrics_round_trip(self):
        self.assertEqual(self.report["rows"], {"train": 800, "calibration": 100, "validation": 100, "test": 200})
        self.assertEqual(set(self.report["models"]), {"full", "without_profile", "without_history"})
        for model in self.report["models"].values():
            self.assertGreaterEqual(model["test"]["roc_auc"], 0)
            self.assertGreaterEqual(model["test"]["log_loss"], 0)
            self.assertTrue(model["test"]["calibration_bins"])
        self.assertFalse(self.report["deployment_validated"])
        self.assertTrue(self.report["synthetic"])
        self.assertEqual(self.report["models"]["full"]["cold_start"]["unseen_user"]["rows"], 200)

    def test_validation_and_test_not_in_vectorizer(self):
        fitting, calibration, validation, _ = split_training(self.train, 42)
        vocab = self.model.bundle["pipeline"].named_steps["vectorizer"].vocabulary_
        self.assertNotIn("userid", vocab)
        self.assertEqual(self.model.bundle["baseline_ctr"], sum(label(r) for r in fitting) / len(fitting))
        # A category added only at inference cannot mutate the fitted vocabulary.
        before = dict(vocab)
        candidate = Candidate("new", "New", category_id="unknown-only-online", brand_id="999999",
                              feature_space=SPACE, feature_price=.1, price_encoding=PRICE_ENCODING)
        scores = self.model.score_candidates(ConsumerProfile("new-user"), [], [candidate])
        self.assertIn("unseen_category:cate_id", scores[0].warnings)
        self.assertEqual(before, vocab)

    def test_out_of_scope_candidate_uses_baseline(self):
        score = self.model.score_candidates(ConsumerProfile("web-id"), [], [Candidate("demo", "Demo")])[0]
        self.assertEqual(score.probability, self.model.bundle["baseline_ctr"])
        self.assertIn("constant_baseline_fallback", score.warnings)

    def test_feature_contract_saved_and_empty_prediction(self):
        spec = json.loads((Path(self.tmp.name) / "feature_spec.json").read_text())
        self.assertEqual(spec, self.model.adapter.spec.to_dict())
        self.assertEqual(self.model.score_candidates(ConsumerProfile("u"), [], []), [])

    def test_identical_train_test_rejected(self):
        with self.assertRaises(ValueError):
            train_behavior(self.train, self.train[:50], self.tmp.name, data_version="bad")

    def test_invalid_labels_rejected(self):
        for row in ({}, {"clk": "purchase"}, {"clk": 2}):
            with self.assertRaises(ValueError):
                label(row)

    def test_single_class_and_empty_cold_start_metrics(self):
        self.assertIsNone(metrics([0, 0], [.1, .2])["roc_auc"])
        self.assertIsNone(metrics([], [])["log_loss"])

    def test_chronological_split_keeps_timestamp_groups_separate(self):
        rows = synthetic_rows(1000)
        for index, row in enumerate(rows):
            row["time_stamp"] = index // 10
            row["clk"] = index % 2
        train, cal, valid, method = split_training(rows, 42)
        self.assertLess(max(r["time_stamp"] for r in train), min(r["time_stamp"] for r in cal))
        self.assertLess(max(r["time_stamp"] for r in cal), min(r["time_stamp"] for r in valid))
        self.assertTrue(method.startswith("chronological"))

    def test_partial_timestamps_rejected(self):
        rows = synthetic_rows(100)
        rows[0]["time_stamp"] = 1
        with self.assertRaises(ValueError):
            split_training(rows, 42)

    def test_data_inspection_and_bounded_read(self):
        path = Path(self.tmp.name) / "sample.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in self.train), encoding="utf-8")
        rows = read_rows(path, 10)
        report = inspect_rows(rows)
        self.assertEqual(report["rows"], 10)
        self.assertFalse(report["price"]["is_cny"])
        self.assertFalse(report["history_future_leakage_verified"])
        self.assertEqual(report["history"]["cate_his"]["separator"], "^")


if __name__ == "__main__":
    unittest.main()
