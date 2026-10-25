"""The same row conversion is used in fitting and online inference."""
from dataclasses import dataclass, asdict
from collections import Counter
import math

PROFILE_FIELDS = ("age_level", "pvalue_level", "shopping_level", "final_gender_code",
                  "occupation", "new_user_class_level")
ITEM_FIELDS = ("cate_id", "brand")
SPACE = "taobaoad_x1"
PRICE_ENCODING = "taobaoad_x1_scaled"
MISSING = "__MISSING__"


def token(value):
    if value is None or str(value).strip().lower() in ("", "nan", "none", "null"):
        return None
    text = str(value).strip()
    try:
        number = float(text)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
    except (ValueError, OverflowError):
        pass
    return text


def sequence(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    if not isinstance(value, (str, list, tuple)):
        raise ValueError("历史序列必须为 ^ 分隔字符串或列表")
    parts = value.split("^") if isinstance(value, str) else value
    return [normalized for part in parts if (normalized := token(part)) is not None]


@dataclass(frozen=True)
class FeatureSpec:
    version: str = "taobaoad-summary-v1"
    feature_space: str = SPACE
    price_encoding: str = PRICE_ENCODING
    sequence_separator: str = "^"
    max_history: int = 50
    include_profile: bool = True
    include_history: bool = True

    def to_dict(self):
        return asdict(self)


class FeatureAdapter:
    def __init__(self, spec: FeatureSpec | None = None):
        self.spec = spec or FeatureSpec()
        if self.spec.version != "taobaoad-summary-v1" or self.spec.feature_space != SPACE or self.spec.price_encoding != PRICE_ENCODING or self.spec.sequence_separator != "^" or self.spec.max_history != 50:
            raise ValueError("不支持的特征约定")

    def from_row(self, row: dict) -> tuple[dict, list[str]]:
        features, missing = {}, []
        fields = ITEM_FIELDS + (PROFILE_FIELDS if self.spec.include_profile else ())
        for key in fields:
            value = token(row.get(key))
            features[key] = value or MISSING
            if value is None:
                missing.append(key)
        try:
            price = float(row["price"])
            if not math.isfinite(price) or not 0 <= price <= 1:
                raise ValueError
            features["price"] = price
            features["price_missing"] = 0.0
        except (KeyError, TypeError, ValueError):
            features["price"] = 0.0
            features["price_missing"] = 1.0
            missing.append("price")
        if self.spec.include_history:
            for history_key, target in (("cate_his", "cate_id"), ("brand_his", "brand")):
                items = sequence(row.get(history_key))[-self.spec.max_history:]
                raw_history = row.get(history_key)
                known = history_key in row and raw_history is not None and not (
                    isinstance(raw_history, float) and math.isnan(raw_history))
                features[history_key + "_missing"] = float(not known)
                features[history_key + "_length"] = float(len(items))
                features[history_key + "_unique"] = float(len(set(items)))
                current = token(row.get(target))
                features[history_key + "_match_rate"] = (
                    Counter(items)[current] / len(items) if items and current else 0.0)
                if not known:
                    missing.append(history_key)
        return features, missing

    def online_rows(self, profile, history: list[dict], candidates) -> tuple[list[dict], list[list[str]], list[list[str]]]:
        rows, missing_fields, warnings = [], [], []
        profile_matches = profile.feature_space == self.spec.feature_space
        values = profile.feature_values() if profile_matches else {}
        # Only observed pre-existing feedback in the same ID namespace enters history.
        trusted = [h for h in history if h.get("source") == "real"
                   and h.get("feature_space") == self.spec.feature_space
                   and h.get("clicked") is True]
        for candidate in candidates:
            row = {key: values.get(key) for key in PROFILE_FIELDS}
            compatible = candidate.feature_space == self.spec.feature_space
            notes = []
            if not profile_matches:
                notes.append("profile_feature_space_mismatch")
            if compatible:
                row.update(cate_id=candidate.category_id, brand=candidate.brand_id)
            else:
                notes.append("candidate_feature_space_mismatch")
            if compatible and candidate.price_encoding == self.spec.price_encoding:
                row["price"] = candidate.feature_price
            else:
                notes.append("price_encoding_unknown")
            if trusted:
                row["cate_his"] = [h.get("category_id") for h in trusted]
                row["brand_his"] = [h.get("brand_id") for h in trusted]
            # Use the same conversion as training; missing history is not fabricated.
            encoded, missing = self.from_row(row)
            rows.append(encoded)
            missing_fields.append(missing)
            warnings.append(notes)
        return rows, missing_fields, warnings
