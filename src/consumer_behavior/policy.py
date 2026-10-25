from dataclasses import dataclass, field, asdict
from enum import Enum
import random

from .behavior import CandidateScore


class Mode(str, Enum):
    ASSISTANT = "assistant"
    SIMULATION = "simulation"


@dataclass(frozen=True)
class Decision:
    mode: str
    action: str
    exposed_ids: list[str]
    clicked_ids: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    policy_version: str = "independent-click-v1"
    explanation_source: str = "deterministic_policy"

    def to_dict(self):
        return asdict(self)


class DecisionPolicy:
    def __init__(self, top_k: int = 3):
        if top_k <= 0:
            raise ValueError("top_k 必须为正整数")
        self.top_k = top_k

    def decide(self, mode: Mode, scores: list[CandidateScore], *, seed: int | None = None) -> Decision:
        mode = Mode(mode)
        if not scores:
            return Decision(mode.value, "no_candidates", [])
        if mode == Mode.ASSISTANT:
            ranked = sorted(scores, key=lambda score: (-score.probability, score.candidate_id))[:self.top_k]
            return Decision(mode.value, "recommend", [s.candidate_id for s in ranked], reasons={
                s.candidate_id: f"通过已知硬条件检查；模型点击分数 {s.probability:.4f}，最终由用户选择"
                for s in ranked})
        rng = random.Random(seed)
        # Each displayed ad is a separate Bernoulli trial: zero or multiple clicks are valid.
        ordered = sorted(scores, key=lambda score: score.candidate_id)
        clicks = [s.candidate_id for s in ordered if rng.random() < s.probability]
        return Decision(mode.value, "click" if clicks else "no_click",
                        [s.candidate_id for s in ordered], clicks,
                        {s.candidate_id: "按独立点击概率采样；该动作策略尚未用真实场景验证" for s in ordered})
