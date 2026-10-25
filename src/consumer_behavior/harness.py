from uuid import uuid4

from .behavior import LIMITATIONS
from .constraints import ConstraintChecker
from .feedback import FeedbackRecorder
from .policy import Mode, DecisionPolicy
from .storage import utc_now


class Harness:
    def __init__(self, provider, model, memory, store, policy=None, checker=None, max_retries=1):
        if not 0 <= max_retries <= 5:
            raise ValueError("重试上限必须在 0–5 之间")
        self.provider, self.model, self.memory, self.store = provider, model, memory, store
        self.policy = policy or DecisionPolicy()
        self.checker = checker or ConstraintChecker()
        self.max_retries = max_retries

    def run(self, profile, state, mode=Mode.ASSISTANT, *, seed=None):
        mode = Mode(mode)
        if not profile.consent:
            raise PermissionError("保存运行轨迹前需要数据授权")
        state.__post_init__()
        history = self.memory.retrieve(profile.user_id, state.need, top_k=50, source="real")
        trace = {"run_id": str(uuid4()), "user_id": profile.user_id, "time": utc_now(),
            "mode": mode.value, "seed": seed, "profile": profile.to_dict(),
            "state_before": state.to_dict(), "memories": history, "attempts": [],
            "limitations": LIMITATIONS, "data_consent": profile.consent}
        try:
            for attempt in range(self.max_retries + 1):
                candidates = self.provider.get_candidates(profile, state)
                ids = [c.candidate_id for c in candidates]
                if len(ids) != len(set(ids)):
                    raise ValueError("候选编号重复")
                checks = [self.checker.check(c, state) for c in candidates]
                feasible_ids = {c.candidate_id for c in checks if c.feasible}
                feasible = [c for c in candidates if c.candidate_id in feasible_ids]
                scores = self.model.score_candidates(profile, history, feasible)
                if len(scores) != len(feasible) or {s.candidate_id for s in scores} != feasible_ids:
                    raise ValueError("模型输出与候选商品不一致")
                decision = self.policy.decide(mode, scores, seed=seed)
                if not set(decision.exposed_ids) <= feasible_ids or not set(decision.clicked_ids) <= set(decision.exposed_ids):
                    raise ValueError("策略输出包含未通过检查的商品")
                if mode == Mode.ASSISTANT and decision.clicked_ids:
                    raise ValueError("助手模式不能替用户产生点击行为")
                final_checks = [self.checker.check(c, state) for c in feasible
                                if c.candidate_id in decision.exposed_ids]
                trace["attempts"].append({"attempt": attempt, "initial_checks": [c.to_dict() for c in checks],
                    "final_checks": [c.to_dict() for c in final_checks]})
                if all(c.feasible for c in final_checks):
                    break
            else:
                raise RuntimeError("输出约束检查失败，已达到重试上限")
            trace.update(status="completed", candidates=[c.to_dict() for c in candidates],
                         scores=[s.to_dict() for s in scores], decision=decision.to_dict(),
                         model_versions=sorted({s.model_version for s in scores}))
            after = state.to_dict()
            if mode == Mode.SIMULATION:
                after["simulated_clicks"] += decision.clicked_ids
            trace["state_after"] = after
            self.store.save_trace(trace)
            if mode == Mode.SIMULATION:
                FeedbackRecorder(self.store).record(profile, trace["run_id"], source="simulated",
                    clicked={cid: cid in decision.clicked_ids for cid in decision.exposed_ids})
                state.simulated_clicks = after["simulated_clicks"]
            return trace
        except Exception as error:
            if "status" not in trace:
                trace.update(status="failed", error={"type": type(error).__name__, "message": str(error)},
                             state_after=state.to_dict())
                self.store.save_trace(trace)
            raise
