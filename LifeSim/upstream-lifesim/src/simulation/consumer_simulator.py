"""LifeSim personalization with an added consumer decision episode.

UserProfile, SimpleMemory, KVMemory, both agents' dialogue methods and the
ConversationSimulator environment/reset lifecycle are the upstream implementations.
"""
import json
import hashlib
from uuid import uuid4

from agents.memory import KVMemory
from simulation.conversation_simulator import ConversationSimulator
from consumer_behavior.profile import ConsumerProfile, ProfileAttribute
from consumer_behavior.state import ConsumerState
from consumer_behavior.feedback import FeedbackRecorder
from consumer_behavior.policy import DecisionPolicy, Mode
from consumer_behavior.behavior import LIMITATIONS
from consumer_behavior.recommendation import RestaurantDecisionModule, RECOMMENDATION_LIMITATIONS
from consumer_behavior.restaurants import RestaurantConstraintChecker
from consumer_behavior.storage import utc_now


class ConsumerSimulator(ConversationSimulator):
    def __init__(self, *, user_agent, assistant_agent, life_event_engine, provider, behavior_model=None,
                 store, retriever_config, retriever_factory=None, max_retries=2, top_k=3):
        super().__init__(None, life_event_engine, user_agent, assistant_agent, logger_silent=True)
        if not 0 <= max_retries <= 5 or top_k <= 0:
            raise ValueError("无效的重试上限或候选数量")
        self.provider, self.behavior_model, self.store = provider, behavior_model, store
        self.retriever_config, self.retriever_factory = retriever_config, retriever_factory
        self.max_retries, self.top_k = max_retries, top_k
        self.checker = RestaurantConstraintChecker()
        self.decision_module = RestaurantDecisionModule(top_k, max_retries, self.checker)
        self.consumer_state = None
        self.last_trace = None
        self.current_memory_source = None

    def _feature_view(self):
        # LifeSim UserProfile remains authoritative; this is only a typed model-input view.
        raw = self.user_profile.to_dict()
        attributes = {key: ProfileAttribute(**value) for key, value in raw.get("consumer_features", {}).items()}
        return ConsumerProfile(str(self.user_profile.user_id), attributes,
            feature_space=raw.get("feature_space", "unknown"), consent=raw.get("consent") is True)

    def _load_personalized_memories(self, source):
        self.current_memory_source = source
        user_id = str(self.user_profile.user_id)
        saved = self.store.memories(user_id, source)
        # A dedicated index is rebuilt from durable observations; never reset other LifeSim indexes.
        scope = hashlib.sha256(f"{user_id}:{source}".encode()).hexdigest()[:24]
        for role, agent in (("user", self.user_agent), ("assistant", self.assistant_agent)):
            config = dict(self.retriever_config, collection_name=f"consumer_{role}_{scope}")
            retriever = self.retriever_factory(config) if self.retriever_factory else None
            entries = [{"query": m["memory_id"], "response": m.get("text", ""),
                        "source": source, "evidence_id": m["memory_id"]} for m in saved if m.get("text")]
            agent.dynamic_memory = KVMemory(config, memories=entries, retriever=retriever)
        return saved

    def _retrieve_context(self, query, source, saved):
        results = self.user_agent.dynamic_memory.search(query, top_k=5) if saved else []
        context = [{"text": item.get("response", item.get("text", "")),
                    "similarity": float(score), "source": source,
                    "evidence_id": item.get("evidence_id"), "retriever": "LifeSim.KVMemory"}
                   for item, score in results]
        if source == "simulated":
            # Real observations can contextualize a twin, but simulations never update the real index.
            context.extend({"text": m.get("text", ""), "source": "real", "evidence_id": m["memory_id"],
                            "retriever": "observed_memory_seed"}
                           for m in self.store.memories(str(self.user_profile.user_id), "real")[-5:])
        return context

    def run_consumer_episode(self, *, mode="assistant", user_input=None, seed=None, state_overrides=None):
        mode = Mode(mode)
        if mode == Mode.SIMULATION and self.behavior_model is None:
            raise ValueError("首版提供餐厅推荐；行为预演需另行接入并验证行为模型")
        feature_view = self._feature_view()
        if not feature_view.consent:
            raise PermissionError("使用个人画像、LLM 与记忆前需要数据授权")
        if mode == Mode.ASSISTANT and (not isinstance(user_input, str) or not user_input.strip()):
            raise ValueError("助手模式需要真实用户的需求输入")
        event = dict(self.life_event_engine.generate_event())
        event.setdefault("sub_intents", [])
        event.setdefault("intent", "获取符合当前需求的消费建议")
        metadata = event.get("consumer", {})
        preferences = self.user_profile.extra.get("consumer_preferences", {})
        overrides = dict(state_overrides or {})
        allowed_fields = {"budget_cny", "location", "time", "category_ids", "allergies", "excluded_ingredients",
                          "required_diet_tags", "preferred_cuisines", "max_distance_meters"}
        if set(overrides) - allowed_fields:
            raise ValueError("消费情境包含未定义字段")
        values = {key: preferences.get(key, []) for key in ("allergies", "excluded_ingredients", "required_diet_tags", "preferred_cuisines")}
        values["max_distance_meters"] = preferences.get("max_distance_meters")
        values["budget_cny"] = metadata.get("budget_cny") if metadata.get("budget_cny") is not None else preferences.get("meal_budget_cny")
        values.update({key: value for key, value in metadata.items() if key in allowed_fields and value is not None})
        values.update(overrides)
        for key in ("allergies", "excluded_ingredients", "required_diet_tags"):
            values[key] = list(dict.fromkeys(preferences.get(key, []) + values.get(key, [])))
        values.setdefault("location", event.get("location"))
        values.setdefault("category_ids", list(metadata.get("category_ids", [])))
        need_text = user_input if mode == Mode.ASSISTANT else metadata.get("need", event.get("life_event", ""))
        state = ConsumerState(need=need_text, **values)
        source = "real" if mode == Mode.ASSISTANT else "simulated"
        if mode == Mode.SIMULATION and self.last_trace and self.last_trace["mode"] == "simulation" and self.last_trace["user_id"] == feature_view.user_id:
            state.simulated_clicks = list(self.last_trace["state_after"]["simulated_clicks"])
        self.reset()
        trace = {"run_id": str(uuid4()), "user_id": feature_view.user_id, "time": utc_now(),
            "mode": mode.value, "seed": seed, "data_consent": True,
            "profile": self.user_profile.to_dict(), "profile_component": "LifeSim.UserProfile",
            "model_feature_view": feature_view.to_dict(), "state_before": state.to_dict(),
            "event": event, "attempts": [], "limitations": list(RECOMMENDATION_LIMITATIONS if mode == Mode.ASSISTANT else LIMITATIONS),
            "behavior_model_enabled": self.behavior_model is not None,
            "decision_task": "personalized_recommendation" if mode == Mode.ASSISTANT else "click_simulation",
            "personalization": {"user_agent": type(self.user_agent).__name__,
                "assistant_agent": type(self.assistant_agent).__name__, "memory": "LifeSim.SimpleMemory + KVMemory",
                "simulator": "LifeSim.ConversationSimulator subclass", "static_profile_text": self.user_agent.get_profile()}}
        try:
            saved = self._load_personalized_memories(source)
            context = self._retrieve_context(state.need, source, saved)
            observed = self.store.memories(feature_view.user_id, "real")
            self.user_agent._build_environment(event)
            # Preserve known profile text; inferred preference summaries stay separate.
            self.assistant_agent.user_preferences_str = self.user_agent.get_profile()
            known_profile = self.user_agent.get_profile()
            inferred = self.user_profile.extra.get("llm_inferred_preferences")
            if inferred:
                known_profile += "\n[以下为未确认的 LLM 推断，不是已知事实]\n" + json.dumps(inferred, ensure_ascii=False)
            self.assistant_agent.user_preferences_str = known_profile
            self.assistant_agent._build_system_prompt(event)
            self.user_agent.conversations.add("消费记忆：" + json.dumps(context, ensure_ascii=False))
            self.assistant_agent.conversations.add("消费记忆：" + json.dumps(context, ensure_ascii=False))
            if mode == Mode.SIMULATION:
                user_result = self.user_agent.respond("", use_dynamic_memory=False, use_emotion_chain=False)
                need_utterance = user_result["response"]
                trace["user_utterance_source"] = "lifesim_user_llm_simulation"
            else:
                need_utterance = user_input
                self.user_agent.conversations.add(f"User: {user_input}")
                trace["user_utterance_source"] = "real_user_input"
            if not isinstance(need_utterance, str) or not need_utterance.strip():
                raise RuntimeError("LifeSim 用户代理未产生有效需求")
            if mode == Mode.ASSISTANT:
                intent_holder = {"value": "获取个性化餐厅建议"}
                def planner(evidence):
                    intent_holder["value"], _ = self.assistant_agent.respond(
                        need_utterance + "\n[消费工具结果]\n" + json.dumps(evidence, ensure_ascii=False),
                        event, use_key_info_memory=bool(saved))
                    return self.assistant_agent.plan_consumer(evidence)
                result = self.decision_module.recommend(profile=feature_view, state=state, provider=self.provider,
                    memories=context, need=need_utterance, planner=planner, attempts=trace["attempts"],
                    behavior_model=self.behavior_model, observed_history=observed)
                decision, candidates, scores, reply = result.decision, result.candidates, result.scores, result.response
                intent = intent_holder["value"]
                feasible = bool(decision.exposed_ids) or any(c["feasible"] for c in trace["attempts"][-1]["initial_checks"])
            else:
                candidates = self.provider.get_candidates(feature_view, state)
                checks = [self.checker.check(c, state) for c in candidates]
                feasible_ids = {c.candidate_id for c in checks if c.feasible}
                feasible = [c for c in candidates if c.candidate_id in feasible_ids]
                scores = self.behavior_model.score_candidates(feature_view, observed, feasible)
                if len(scores) != len(feasible) or {s.candidate_id for s in scores} != feasible_ids:
                    raise ValueError("点击模型输出与候选不一致")
                decision = DecisionPolicy(self.top_k).decide(mode, scores, seed=seed)
                final_checks = [self.checker.check(c, state) for c in feasible if c.candidate_id in decision.exposed_ids]
                trace["attempts"].append({"attempt": 0, "initial_checks": [c.to_dict() for c in checks],
                                         "final_checks": [c.to_dict() for c in final_checks]})
                if not all(c.feasible for c in final_checks):
                    raise RuntimeError("行为预演的最终约束已变化")
                intent = "点击层面的消费者行为预演"
                reply = self.user_agent.explain_consumer_action({"state": state.to_dict(), "memories": context,
                    "candidates": [c.to_dict() for c in feasible], "scores": [s.to_dict() for s in scores],
                    "sampled_decision": decision.to_dict()}) if feasible else "没有可行候选。"
            state.simulated_clicks.extend(decision.clicked_ids)
            self.consumer_state = state
            trace.update(status="completed", memories=context, candidates=[c.to_dict() for c in candidates],
                scores=[s.to_dict() for s in scores], decision=decision.to_dict(), state_after=state.to_dict(),
                model_versions=sorted({s.model_version for s in scores}), intent=intent, response=reply,
                response_source="llm_inference" if feasible else "constraint_checker",
                llm_model=getattr(self.assistant_agent.model if mode == Mode.ASSISTANT else self.user_agent.model, "model", "injected_test_model"),
                conversations={"user": self.user_agent.conversations.get(), "assistant": self.assistant_agent.conversations.get()})
            self.store.save_trace(trace)
            if mode == Mode.SIMULATION:
                FeedbackRecorder(self.store).record(feature_view, trace["run_id"], source="simulated",
                    clicked={cid: cid in decision.clicked_ids for cid in decision.exposed_ids})
            self.dialogue_log.append({"event": event, "consumer": trace})
            self.last_trace = trace
            return trace
        except Exception as error:
            if "status" not in trace:
                trace.update(status="failed", error={"type": type(error).__name__, "message": str(error)},
                             state_after=state.to_dict())
                self.store.save_trace(trace)
            raise

    def run_choice_rehearsal(self, event, evidence, *, rounds=2):
        """Use original LifeSim turns, then the consumer-agent structured choice extension.

        This private episode is isolated from真人访谈 and real feedback memory.
        No trained click predictor is required and no simulated utterance becomes a real fact.
        """
        if type(rounds) is not int or not 1 <= rounds <= 3:
            raise ValueError("消费预演只支持1到3轮模拟交流")
        self.reset()
        scene = dict(event)
        scene["dialogue_scene"] += "\n[消费情境与候选事实]\n" + json.dumps(evidence, ensure_ascii=False)
        self.user_agent._build_environment(scene)
        self.user_agent._build_chat_system_prompt()
        self.assistant_agent.user_preferences_str = self.user_agent.get_profile()
        self.assistant_agent._build_system_prompt(scene)
        history = "\n[截至快照的真人历史与原生记忆检索]\n" + json.dumps(evidence.get("history", []), ensure_ascii=False)
        self.user_agent.conversations.add(history)
        self.assistant_agent.conversations.add(history)
        episode = {"dialogue": [], "source": "lifesim_llm_simulation", "event": event}
        assistant_response = ""
        for round_index in range(1, rounds + 1):
            result = self.user_agent.respond(assistant_response, use_dynamic_memory=False, use_emotion_chain=False)
            if result["action"] == self.user_agent.action_space.END_CONVERSATION:
                break
            if not isinstance(result.get("response"), str) or not result["response"].strip():
                raise RuntimeError("LifeSim孪生用户未产生有效模拟话语")
            self.update_interface_dialog("simulated_user", result["response"], episode, round_index)
            intent, assistant_response = self.assistant_agent.respond(result["response"], scene, use_key_info_memory=False)
            if not isinstance(assistant_response, str) or not assistant_response.strip():
                raise RuntimeError("LifeSim模拟助手未产生有效话语")
            self.update_interface_dialog("simulated_assistant", assistant_response, episode, round_index, intent=intent)
        for attempt in range(2):
            prediction = self.user_agent.predict_choice(evidence)
            allowed = set(evidence["allowed_ids"])
            choice, ranking, reason = prediction.get("choice_id"), prediction.get("ranking"), prediction.get("reason")
            valid = isinstance(choice, str) and choice in allowed | {"none"}
            valid = valid and isinstance(ranking, list) and all(isinstance(x, str) and x in allowed for x in ranking)
            valid = valid and len(ranking) == len(set(ranking)) and isinstance(reason, str) and bool(reason.strip())
            valid = valid and ((choice == "none" and not ranking) or (bool(ranking) and ranking[0] == choice))
            if valid:
                prediction.update(probabilities=None, source="lifesim_deepseek_simulation")
                return {"prediction": prediction, "simulation_dialogue": episode["dialogue"],
                    "model": getattr(self.user_agent.model, "model", "injected_test_model"),
                    "native_components": ["LifeSim.UserProfile", "LifeSim.SimpleMemory", "LifeSim.KVMemory",
                        "LifeSim.UserAgent.respond", "LifeSim.AssistantAgent.respond", "LifeSim.ConversationSimulator"],
                    "retrieved_memories": evidence.get("memories", [])}
            evidence = dict(evidence, previous_validation_error="选择或排序编号无效、越过硬条件，或格式不符；重新输出JSON。none的ranking必须为空。")
        raise ValueError("孪生预测未通过候选与硬条件核验")

    def record_consumer_feedback(self, run_id, *, clicked=None, selected_id=None, satisfaction=None, comment="", rejected_reasons=None):
        view = self._feature_view()
        feedback = FeedbackRecorder(self.store).record(view, run_id, source="real", clicked=clicked,
            selected_id=selected_id, satisfaction=satisfaction, comment=comment, rejected_reasons=rejected_reasons)
        if self.current_memory_source != "real":
            self.reset()
            self._load_personalized_memories("real")
        else:
        # The original LifeSim KVMemory stores explicit feedback for future personalized retrieval.
            for memory in self.store.memories(view.user_id, "real"):
                if memory.get("run_id") == run_id:
                    for agent in (self.user_agent, self.assistant_agent):
                        agent.dynamic_memory.add_item({"query": memory["memory_id"], "response": memory["text"],
                            "source": "real", "evidence_id": memory["memory_id"]})
        self.user_agent.conversations.add("用户明确反馈：" + json.dumps(feedback, ensure_ascii=False))
        self.assistant_agent.conversations.add("用户明确反馈：" + json.dumps(feedback, ensure_ascii=False))
        # Preserve observed counters on the authoritative upstream profile, with provenance.
        self.user_profile.extra["consumer_observations"] = {
            key: {"value": attr.value, "source": attr.source, "confidence": attr.confidence}
            for key, attr in view.attributes.items() if attr.source == "real_feedback"}
        if self.user_profile.preferences_value:
            original_trace = self.store.get_trace(run_id, view.user_id)
            self.assistant_agent.summarize(original_trace["event"], list(self.user_profile.preferences_value),
                                          use_profile_memory=True, use_key_info_memory=False)
            self.user_profile.extra["llm_inferred_preferences"] = {
                "source": "llm_inference", "values": self.assistant_agent.user_preferences}
        self.user_agent._build_profile(str(self.user_profile))
        return feedback
