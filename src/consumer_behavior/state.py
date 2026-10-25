from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import math


@dataclass
class ConsumerState:
    budget_cny: float | None = None
    location: str | None = None
    need: str = ""
    category_ids: list[str] = field(default_factory=list)
    time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    simulated_clicks: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    excluded_ingredients: list[str] = field(default_factory=list)
    required_diet_tags: list[str] = field(default_factory=list)
    preferred_cuisines: list[str] = field(default_factory=list)
    max_distance_meters: float | None = None

    def __post_init__(self):
        if self.budget_cny is not None:
            if isinstance(self.budget_cny, bool) or not math.isfinite(self.budget_cny) or self.budget_cny < 0:
                raise ValueError("预算必须为非负有限人民币金额")
        datetime.fromisoformat(self.time)
        if self.max_distance_meters is not None and (isinstance(self.max_distance_meters, bool)
                or not math.isfinite(self.max_distance_meters) or self.max_distance_meters < 0):
            raise ValueError("距离上限必须为非负有限米数")
        for values in (self.allergies, self.excluded_ingredients, self.required_diet_tags, self.preferred_cuisines):
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError("偏好与禁忌必须为非空字符串列表")

    def to_dict(self):
        return asdict(self)
