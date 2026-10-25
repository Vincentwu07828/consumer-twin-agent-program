"""Questionnaire answers become observed LifeSim persona facts, without LLM guessing."""
import math
from .storage import utc_now

QUESTIONNAIRE = [
    {"id": "preferred_cuisines", "label": "喜欢哪些菜系或口味？", "type": "string_list", "required": False},
    {"id": "meal_budget_cny", "label": "通常每人一餐的预算是多少元？", "type": "number", "required": False},
    {"id": "excluded_ingredients", "label": "有哪些明确不吃的食材？", "type": "string_list", "required": False},
    {"id": "allergies", "label": "有哪些需要避开的过敏原？", "type": "string_list", "required": False},
    {"id": "required_diet_tags", "label": "有哪些必须满足的饮食要求？", "type": "string_list", "required": False},
    {"id": "max_distance_meters", "label": "最多接受多远的餐厅（米）？", "type": "number", "required": False},
    {"id": "usual_dinner_time", "label": "通常几点吃晚餐？", "type": "time", "required": False},
]


def questionnaire_profile(user_id: str, answers: dict, *, consent: bool):
    if consent is not True:
        raise PermissionError("保存问卷和使用个性化建议需要明确授权")
    if not isinstance(user_id, str) or not user_id.strip() or not isinstance(answers, dict):
        raise ValueError("无效用户编号或问卷")
    schema = {item["id"]: item for item in QUESTIONNAIRE}
    if set(answers) - set(schema):
        raise ValueError("问卷包含未定义字段")
    for key, value in answers.items():
        if value is None:
            continue
        kind = schema[key]["type"]
        if kind == "string_list" and (not isinstance(value, list)
                or any(not isinstance(v, str) or not v.strip() for v in value)):
            raise ValueError(f"{key} 必须为字符串列表")
        if kind == "number" and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError(f"{key} 必须为非负有限数值")
        if kind == "time":
            from datetime import time
            if not isinstance(value, str) or len(value) != 5:
                raise ValueError("通常晚餐时间必须为 HH:MM")
            time.fromisoformat(value)
    facts = {key: value for key, value in answers.items() if value is not None}
    labels = {item["id"]: item["label"] for item in QUESTIONNAIRE}
    preferences = [f"{labels[key]} 用户回答：{value}" for key, value in facts.items()]
    return {"user_id": user_id, "age": None, "gender": None, "income": None,
        "employment": None, "area": None, "personality": [], "preferences": preferences,
        "preferences_value": {}, "consent": True, "consumer_features": {}, "feature_space": "unknown",
        "consumer_preferences": facts,
        "questionnaire": {"source": "user_questionnaire", "time": utc_now(), "answers": facts},
        "attribute_provenance": {key: {"source": "user_questionnaire", "confidence": 1.0} for key in facts}}
