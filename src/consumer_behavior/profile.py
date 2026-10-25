from dataclasses import dataclass, field, asdict
from typing import Any
import math


@dataclass(frozen=True)
class ProfileAttribute:
    value: Any
    source: str
    confidence: float = 1.0

    def __post_init__(self):
        if not self.source or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("画像属性必须保留来源及 0–1 的置信度")


@dataclass
class ConsumerProfile:
    user_id: str
    attributes: dict[str, ProfileAttribute] = field(default_factory=dict)
    feature_space: str = "unknown"
    preferred_categories: list[str] = field(default_factory=list)
    consent: bool = False

    def __post_init__(self):
        if not self.user_id:
            raise ValueError("user_id 不能为空")

    def feature_values(self) -> dict:
        # User IDs are deliberately excluded from model input.
        return {key: attr.value for key, attr in self.attributes.items()
                if attr.value is not None and attr.confidence > 0
                and attr.source != "llm_inference"}

    def to_dict(self) -> dict:
        return asdict(self)
