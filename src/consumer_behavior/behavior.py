from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Protocol
import math

import joblib
import numpy as np

from .features import FeatureAdapter, FeatureSpec, MISSING

LIMITATIONS = [
    "独立点击概率，不是购买概率或互斥选择概率",
    "不支持交易金额或消费券政策增量效果推断",
    "候选过滤和曝光改变可能造成分布偏移，需独立校准",
]


@dataclass(frozen=True)
class CandidateScore:
    candidate_id: str
    probability: float
    task: str
    model_version: str
    scope: str
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.task != "click" or not math.isfinite(self.probability) or not 0 <= self.probability <= 1:
            raise ValueError("预测必须是 0–1 的点击概率")

    def to_dict(self):
        return asdict(self)


class ClickScorer(Protocol):
    def score_candidates(self, profile, history, candidates) -> list[CandidateScore]: ...


class ConstantClickModel:
    def __init__(self, click_rate: float, version="demo-constant-v1", scope="demo_unvalidated"):
        if not math.isfinite(click_rate) or not 0 <= click_rate <= 1:
            raise ValueError("基准点击率必须在 0–1 内")
        self.click_rate, self.version, self.scope = click_rate, version, scope

    def score_candidates(self, profile, history, candidates):
        return [CandidateScore(c.candidate_id, self.click_rate, "click", self.version, self.scope,
                               warnings=["constant_baseline_no_personalization"])
                for c in candidates]


class BehaviorModel:
    """Loads a local fitted preprocessor/classifier/calibrator bundle."""
    def __init__(self, bundle: dict):
        if bundle.get("artifact_version") != 1 or bundle.get("task") != "click":
            raise ValueError("不支持的模型文件或任务类型")
        self.bundle = bundle
        self.adapter = FeatureAdapter(FeatureSpec(**bundle["feature_spec"]))

    @classmethod
    def load(cls, path: str | Path):
        # joblib artifacts must be generated locally or come from a trusted source.
        return cls(joblib.load(path))

    def score_candidates(self, profile, history, candidates):
        if not candidates:
            return []
        rows, missing, warnings = self.adapter.online_rows(profile, history, candidates)
        classifier = self.bundle["pipeline"]
        vectorizer = classifier.named_steps["vectorizer"]
        raw = classifier.decision_function(rows)
        probabilities = self.bundle["calibrator"].predict_proba(np.asarray(raw).reshape(-1, 1))[:, 1]
        result = []
        for i, candidate in enumerate(candidates):
            notes = list(warnings[i])
            for key, value in rows[i].items():
                if isinstance(value, str) and value != MISSING and f"{key}={value}" not in vectorizer.vocabulary_:
                    notes.append(f"unseen_category:{key}")
            probability = float(probabilities[i])
            if "candidate_feature_space_mismatch" in notes or not self.bundle["validation_improved"]:
                probability = float(self.bundle["baseline_ctr"])
                notes.append("constant_baseline_fallback")
            if missing[i]:
                notes.append("missing_features_out_of_distribution")
            result.append(CandidateScore(candidate.candidate_id, probability, "click",
                self.bundle["model_version"], self.bundle["scope"], missing[i], notes, rows[i]))
        return result
