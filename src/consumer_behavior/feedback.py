from uuid import uuid4
import json

from .storage import EventStore, utc_now
from .profile import ProfileAttribute


class FeedbackRecorder:
    def __init__(self, store: EventStore):
        self.store = store

    def record(self, profile, run_id: str, *, source: str, clicked: dict[str, bool] | None = None,
               selected_id: str | None = None, satisfaction: int | None = None,
               comment: str = "", rejected_reasons: dict[str, str] | None = None) -> dict:
        if not profile.consent:
            raise PermissionError("记录个人反馈前需要数据授权")
        if source not in ("real", "simulated"):
            raise ValueError("反馈来源必须为 real 或 simulated")
        if not isinstance(comment, str) or (selected_id is not None and not isinstance(selected_id, str)):
            raise ValueError("反馈说明和选择编号必须为文本")
        trace = self.store.get_trace(run_id, profile.user_id)
        if source == "simulated" and trace["mode"] != "simulation":
            raise ValueError("助手建议不能标记为模拟行为")
        if source == "real" and trace["mode"] != "assistant":
            raise ValueError("模拟运行不能写成真实用户反馈")
        exposed = set(trace["decision"]["exposed_ids"])
        clicked = dict(clicked or {})
        if not set(clicked) <= exposed or any(type(value) is not bool for value in clicked.values()):
            raise ValueError("点击反馈必须是已展示商品的明确布尔标签")
        if selected_id is not None and selected_id not in exposed:
            raise ValueError("实际选择必须来自已展示商品")
        if satisfaction is not None and (type(satisfaction) is not int or not 1 <= satisfaction <= 5):
            raise ValueError("满意度必须为 1–5 的整数")
        rejected_reasons = dict(rejected_reasons or {})
        if not set(rejected_reasons) <= exposed or any(not isinstance(reason, str) or not reason.strip()
                for reason in rejected_reasons.values()) or selected_id in rejected_reasons:
            raise ValueError("拒绝原因必须对应已展示且未被选择的候选")
        if source == "simulated":
            expected = {cid: cid in trace["decision"]["clicked_ids"] for cid in exposed}
            if clicked != expected or selected_id is not None or satisfaction is not None or rejected_reasons:
                raise ValueError("模拟反馈必须忠实记录采样点击，不生成交易或满意度标签")
        feedback = {"feedback_id": str(uuid4()), "user_id": profile.user_id, "run_id": run_id,
                    "source": source, "time": utc_now(), "clicked": clicked,
                    "selected_id": selected_id, "satisfaction": satisfaction, "comment": comment,
                    "rejected_reasons": rejected_reasons,
                    "model_versions": trace["model_versions"]}
        items = {c["candidate_id"]: c for c in trace["candidates"]}
        memories = []
        # Unreported clicks remain unknown. Selection is not converted into a purchase.
        for cid, label in clicked.items():
            item = items[cid]
            memories.append({"memory_id": str(uuid4()), "user_id": profile.user_id,
                "source": source, "run_id": run_id, "time": feedback["time"],
                "candidate_id": cid, "category_id": item["category_id"], "brand_id": item["brand_id"],
                "feature_space": item["feature_space"], "clicked": label,
                "text": f"{item['name']} 点击={label} {comment}"})
        if not clicked and (selected_id is not None or satisfaction is not None or comment or rejected_reasons):
            selected_item = items.get(selected_id, {})
            selected_name = selected_item.get("name")
            rejected_names = {items[cid]["name"]: reason for cid, reason in rejected_reasons.items()}
            memories.append({"memory_id": str(uuid4()), "user_id": profile.user_id,
                "source": source, "run_id": run_id, "time": feedback["time"],
                "selected_id": selected_id, "satisfaction": satisfaction,
                "selected_name": selected_name, "rejected_reasons": rejected_reasons,
                "cuisines": selected_item.get("cuisines", []),
                "event_type": "explicit_choice_feedback",
                "text": f"实际选择：{selected_name or '未报告'}；菜系：{selected_item.get('cuisines', [])}；满意度：{satisfaction}；明确拒绝原因：{rejected_names}；用户补充：{comment}"})
        with self.store.connection:
            if self.store.connection.execute(
                    "SELECT 1 FROM feedback WHERE run_id=? AND source=?", (run_id, source)).fetchone():
                raise ValueError("该运行已记录反馈，不能重复累加同一次曝光")
            self.store.connection.execute("INSERT INTO feedback VALUES (?, ?, ?, ?, ?)",
                (feedback["feedback_id"], run_id, profile.user_id, source, json.dumps(feedback, ensure_ascii=False)))
            for memory in memories:
                self.store.connection.execute("INSERT INTO memories VALUES (?, ?, ?, ?)",
                    (memory["memory_id"], profile.user_id, source, json.dumps(memory, ensure_ascii=False)))
        if source == "real":
            observed = [m for m in self.store.memories(profile.user_id, "real") if "clicked" in m]
            profile.attributes["observed_click_count"] = ProfileAttribute(
                sum(m["clicked"] for m in observed), "real_feedback", 1.0)
            profile.attributes["observed_labeled_exposure_count"] = ProfileAttribute(
                len(observed), "real_feedback", 1.0)
            rows = self.store.connection.execute("SELECT payload FROM feedback WHERE user_id=? AND source='real'", (profile.user_id,)).fetchall()
            profile.attributes["observed_selection_count"] = ProfileAttribute(
                sum(json.loads(row[0]).get("selected_id") is not None for row in rows), "real_feedback", 1.0)
        return feedback
