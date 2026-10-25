from dataclasses import dataclass, field, asdict
from typing import Protocol
import json
import math
from pathlib import Path

from .profile import ConsumerProfile
from .state import ConsumerState


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    name: str
    category_id: str | None = None
    brand_id: str | None = None
    price_cny: float | None = None
    in_stock: bool | None = None
    source: str = "demo"
    feature_space: str = "unknown"
    feature_price: float | None = None
    price_encoding: str = "unknown"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.candidate_id or not self.name or not self.source:
            raise ValueError("候选商品必须有编号、名称、来源")
        for price in (self.price_cny, self.feature_price):
            if price is not None and (isinstance(price, bool) or not math.isfinite(price) or price < 0):
                raise ValueError("价格必须为非负有限数值")
        if self.in_stock is not None and type(self.in_stock) is not bool:
            raise ValueError("in_stock 必须为布尔值或未知")

    def to_dict(self):
        return asdict(self)


class CandidateProvider(Protocol):
    def get_candidates(self, profile: ConsumerProfile, state: ConsumerState) -> list[Candidate]: ...


class LocalCandidateProvider:
    def __init__(self, candidates: list[Candidate]):
        ids = [candidate.candidate_id for candidate in candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("候选商品编号重复")
        self.candidates = list(candidates)

    @classmethod
    def from_json(cls, path: str | Path):
        return cls([Candidate(**item) for item in json.loads(Path(path).read_text(encoding="utf-8"))])

    def get_candidates(self, profile, state):
        return [c for c in self.candidates if not state.category_ids
                or c.category_id in state.category_ids]
