"""Restaurant data contract; actual facts must come from a catalogue/provider, never the LLM."""
from dataclasses import dataclass, field
import json
import math
from pathlib import Path

from .candidates import Candidate, LocalCandidateProvider
from .constraints import ConstraintChecker, ConstraintResult


def names(values):
    return {value.strip().casefold() for value in values}


@dataclass(frozen=True)
class RestaurantCandidate(Candidate):
    category_id: str | None = "restaurant"
    source: str = "user_provided"
    cuisines: list[str] = field(default_factory=list)
    address: str | None = None
    distance_meters: float | None = None
    is_open: bool | None = None
    ingredients: list[str] | None = None
    allergens: list[str] | None = None
    diet_tags: list[str] | None = None
    allergen_info_verified: bool = False
    price_basis: str = "per_person_estimate"

    def __post_init__(self):
        super().__post_init__()
        if self.distance_meters is not None and (isinstance(self.distance_meters, bool)
                or not math.isfinite(self.distance_meters) or self.distance_meters < 0):
            raise ValueError("餐厅距离必须为非负有限米数")
        if self.is_open is not None and type(self.is_open) is not bool:
            raise ValueError("营业状态必须为布尔值或未知")
        if type(self.allergen_info_verified) is not bool:
            raise ValueError("过敏原信息核验标志必须为布尔值")
        for values in (self.cuisines, self.ingredients, self.allergens, self.diet_tags):
            if values is not None and (not isinstance(values, list)
                    or any(not isinstance(v, str) or not v.strip() for v in values)):
                raise ValueError("餐厅标签必须为字符串列表或未知")


class RestaurantCandidateProvider(LocalCandidateProvider):
    @classmethod
    def from_json(cls, path: str | Path):
        items = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(items, list):
            raise ValueError("餐厅目录必须为列表")
        return cls([RestaurantCandidate(**item) for item in items])


class RestaurantConstraintChecker(ConstraintChecker):
    def check(self, candidate, state):
        base = super().check(candidate, state)
        if not isinstance(candidate, RestaurantCandidate):
            return base
        reasons, warnings = list(base.reasons), list(base.warnings)
        if candidate.is_open is False:
            reasons.append("restaurant_closed")
        elif candidate.is_open is None:
            warnings.append("opening_status_unknown")
        if state.max_distance_meters is not None:
            if candidate.distance_meters is None:
                reasons.append("distance_unknown")
            elif candidate.distance_meters > state.max_distance_meters:
                reasons.append("too_far")
        if state.allergies:
            if candidate.allergens is None or not candidate.allergen_info_verified:
                reasons.append("allergen_information_unverified")
            elif names(state.allergies) & names(candidate.allergens):
                reasons.append("allergen_conflict")
        if state.excluded_ingredients:
            if candidate.ingredients is None:
                reasons.append("ingredient_information_unknown")
            elif names(state.excluded_ingredients) & names(candidate.ingredients):
                reasons.append("excluded_ingredient")
        if state.required_diet_tags:
            if candidate.diet_tags is None:
                reasons.append("diet_information_unknown")
            elif not names(state.required_diet_tags) <= names(candidate.diet_tags):
                reasons.append("diet_requirement_not_met")
        if candidate.price_basis == "per_person_estimate":
            warnings.append("price_is_per_person_estimate")
        return ConstraintResult(candidate.candidate_id, not reasons, tuple(reasons), tuple(warnings))
