from dataclasses import dataclass, asdict
from .candidates import Candidate
from .state import ConsumerState


@dataclass(frozen=True)
class ConstraintResult:
    candidate_id: str
    feasible: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self):
        return asdict(self)


class ConstraintChecker:
    def check(self, candidate: Candidate, state: ConsumerState) -> ConstraintResult:
        reasons, warnings = [], []
        if candidate.in_stock is False:
            reasons.append("out_of_stock")
        elif candidate.in_stock is None:
            warnings.append("stock_unknown")
        if state.budget_cny is not None:
            if candidate.price_cny is None:
                reasons.append("actual_price_unknown")
            elif candidate.price_cny > state.budget_cny:
                reasons.append("over_budget")
        elif candidate.price_cny is None:
            warnings.append("actual_price_unknown")
        if state.category_ids and candidate.category_id not in state.category_ids:
            reasons.append("category_mismatch")
        return ConstraintResult(candidate.candidate_id, not reasons, tuple(reasons), tuple(warnings))
