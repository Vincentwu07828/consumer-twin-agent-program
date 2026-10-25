"""Independent recommendation decision module. Requires no trained behavior model."""
from dataclasses import dataclass
from .policy import Decision
from .restaurants import RestaurantConstraintChecker

RECOMMENDATION_LIMITATIONS = [
    "排序与理由由 LLM 根据当前画像和记忆生成，尚未用真实选择数据评价",
    "价格、营业、距离与食材信息以候选来源为准；未知信息保持未知",
    "推荐顺序不表示点击或购买概率，也不代表用户已经做出选择",
]


@dataclass
class RecommendationResult:
    decision: Decision
    candidates: list
    scores: list
    attempts: list
    response: str


class RestaurantDecisionModule:
    def __init__(self, top_k=3, max_retries=2, checker=None):
        if top_k <= 0 or not 0 <= max_retries <= 5:
            raise ValueError("无效的推荐数量或重试上限")
        self.top_k, self.max_retries = top_k, max_retries
        self.checker = checker or RestaurantConstraintChecker()

    def recommend(self, *, profile, state, provider, memories, need, planner,
                  attempts, behavior_model=None, observed_history=None):
        for attempt in range(self.max_retries + 1):
            candidates = provider.get_candidates(profile, state)
            ids = [item.candidate_id for item in candidates]
            if len(ids) != len(set(ids)):
                raise ValueError("候选编号重复")
            checks = [self.checker.check(item, state) for item in candidates]
            allowed = {check.candidate_id for check in checks if check.feasible}
            feasible = [item for item in candidates if item.candidate_id in allowed]
            scores = []
            if behavior_model is not None and feasible:
                scores = behavior_model.score_candidates(profile, observed_history or [], feasible)
                if len(scores) != len(feasible) or {score.candidate_id for score in scores} != allowed:
                    raise ValueError("可选行为模型输出与候选不一致")
            current = {"attempt": attempt, "initial_checks": [check.to_dict() for check in checks]}
            attempts.append(current)
            if not feasible:
                current["final_checks"] = []
                return RecommendationResult(Decision("assistant", "no_candidates", []), candidates, scores, attempts,
                    "目前没有符合已知条件的候选，请补充餐厅信息或调整预算与距离。")
            evidence = {"task": "restaurant_recommendation", "need": need, "state": state.to_dict(),
                "memories": memories, "candidates": [item.to_dict() for item in feasible],
                "allowed_ids": sorted(allowed), "top_k": self.top_k,
                "limitations": RECOMMENDATION_LIMITATIONS,
                "previous_validation_error": attempts[-2].get("planner_error") if len(attempts) > 1 else None}
            if scores:
                evidence["optional_behavior_scores"] = [score.to_dict() for score in scores]
            try:
                proposal = planner(evidence)
                if not isinstance(proposal, dict):
                    raise ValueError("LLM 建议必须为对象")
                selected, reasons = proposal.get("recommended_ids"), proposal.get("reasons")
                if not isinstance(selected, list) or any(not isinstance(cid, str) for cid in selected):
                    raise ValueError("LLM 建议编号必须为列表")
                if len(selected) > self.top_k or len(set(selected)) != len(selected) or not set(selected) <= allowed:
                    raise ValueError("LLM 建议包含不可行、重复或过多候选")
                if not isinstance(reasons, dict) or set(reasons) != set(selected) or any(
                        not isinstance(reason, str) or not reason.strip() for reason in reasons.values()):
                    raise ValueError("LLM 必须为每个建议提供理由")
                final = [self.checker.check(item, state) for item in feasible if item.candidate_id in selected]
                current["final_checks"] = [check.to_dict() for check in final]
                if not all(check.feasible for check in final):
                    raise ValueError("输出时餐厅条件已变化，需要重新推荐")
            except ValueError as error:
                current["planner_error"] = str(error)
                if attempt == self.max_retries:
                    raise
                continue
            decision = Decision("assistant", "recommend" if selected else "no_recommendation", selected,
                reasons=reasons, policy_version="lifesim-personalized-recommendation-v2", explanation_source="llm_inference")
            by_id = {item.candidate_id: item for item in feasible}
            reply = "\n".join(f"{by_id[cid].name}：{reasons[cid]}" for cid in selected) or "暂不建议当前候选。"
            return RecommendationResult(decision, candidates, scores, attempts, reply)
        raise RuntimeError("已达到推荐重试上限")
