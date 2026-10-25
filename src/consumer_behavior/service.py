"""Framework service contract for the upcoming questionnaire and restaurant frontend."""
import copy
from datetime import datetime, timezone
from uuid import uuid4

from .questionnaire import QUESTIONNAIRE, questionnaire_profile
from .intent import meal_intent
from .restaurants import RestaurantCandidate, RestaurantCandidateProvider
from .lifesim_runtime import create_simulator


class RestaurantAgentService:
    def __init__(self, config, store, *, simulator_factory=None):
        self.config, self.store = copy.deepcopy(config), store
        self.factory = simulator_factory or create_simulator
        self.simulators = {}

    def questionnaire(self):
        return {"questions": copy.deepcopy(QUESTIONNAIRE), "profile_component": "LifeSim.UserProfile"}

    def create_session(self, answers, consent):
        session_id = str(uuid4())
        profile = questionnaire_profile(session_id, answers, consent=consent)
        self.store.save_profile(profile)
        return {"session_id": session_id, "profile": profile, "status": "profile_created"}

    def profile(self, session_id):
        return self.store.get_profile(session_id)

    def check_intent(self, session_id, *, now=None, explicit_hunger=None, last_meal_at=None):
        profile = self.profile(session_id)
        return meal_intent(now=now or datetime.now(timezone.utc).isoformat(),
            explicit_hunger=explicit_hunger, last_meal_at=last_meal_at,
            usual_dinner_time=profile.get("consumer_preferences", {}).get("usual_dinner_time"))

    def _simulator(self, session_id):
        if session_id not in self.simulators:
            config = copy.deepcopy(self.config)
            config["profile"] = self.profile(session_id)
            config.pop("behavior_model_path", None)
            config["candidate_type"] = "restaurant"
            self.simulators[session_id] = self.factory(config, store=self.store)
        return self.simulators[session_id]

    def recommend(self, session_id, *, need="", context=None, candidates=None,
                  explicit_hunger=None, last_meal_at=None):
        context = dict(context or {})
        if not isinstance(need, str):
            raise ValueError("消费需求必须为文本")
        intent = self.check_intent(session_id, now=context.get("time"), explicit_hunger=explicit_hunger,
                                   last_meal_at=last_meal_at)
        if explicit_hunger is False:
            return {"status": "no_need", "intent": intent, "recommendations": []}
        if need.strip():
            intent.update(intent="dining", confirmed=True, requires_confirmation=False,
                          source="user_restaurant_request")
        if not need.strip() and explicit_hunger is not True:
            return {"status": "needs_confirmation", "intent": intent, "recommendations": []}
        effective_budget = context.get("budget_cny", self.profile(session_id).get("consumer_preferences", {}).get("meal_budget_cny"))
        if effective_budget is None:
            return {"status": "needs_budget", "intent": intent, "recommendations": [],
                    "message": "这次每人就餐预算大约是多少元？"}
        from .state import ConsumerState
        ConsumerState(need=need, **context)
        requested = None
        if candidates is not None:
            if not isinstance(candidates, list):
                raise ValueError("餐厅候选必须为列表")
            requested = RestaurantCandidateProvider([RestaurantCandidate(**item) for item in candidates])
        simulator = self._simulator(session_id)
        # New consumer event, while profile and durable LifeSim memories persist across requests.
        event = {"time": context.get("time", datetime.now(timezone.utc).isoformat()),
            "location": context.get("location"), "weather": None, "event": "餐厅推荐",
            "life_event": need.strip() or "用户确认有用餐需求", "intent": "推荐符合预算和饮食偏好的餐厅",
            "sub_intents": [], "consumer": {"category_ids": ["restaurant"]}}
        simulator.life_event_engine.set_event_sequence_by_profile_and_events(simulator.user_profile.to_dict(), [event])
        original_provider = simulator.provider
        if requested is not None:
            simulator.provider = requested
        try:
            trace = simulator.run_consumer_episode(mode="assistant", user_input=need.strip() or "请推荐适合我的餐厅",
                                                  state_overrides=context)
        finally:
            simulator.provider = original_provider
        by_id = {item["candidate_id"]: item for item in trace["candidates"]}
        checks = {check["candidate_id"]: check for check in trace["attempts"][-1]["initial_checks"]}
        recommendations = [{"rank": rank, "restaurant": by_id[cid], "reason": trace["decision"]["reasons"][cid],
                            "warnings": checks[cid]["warnings"]}
                           for rank, cid in enumerate(trace["decision"]["exposed_ids"], 1)]
        return {"status": trace["decision"]["action"], "run_id": trace["run_id"], "intent": intent,
            "recommendations": recommendations, "message": trace["response"],
            "filtered_out": [{"candidate_id": cid, "reasons": check["reasons"]} for cid, check in checks.items() if not check["feasible"]],
            "decision_source": "lifesim_deepseek_recommendation", "behavior_model_required": False}

    def feedback(self, session_id, run_id, **feedback):
        self.store.get_trace(run_id, session_id)
        result = self._simulator(session_id).record_consumer_feedback(run_id, **feedback)
        self.store.save_profile(self.simulators[session_id].user_profile.to_dict())
        return {"status": "feedback_recorded", "feedback_id": result["feedback_id"]}

    def clear_session(self, session_id):
        self.store.get_profile(session_id)
        self.store.clear_user(session_id)
        simulator = self.simulators.pop(session_id, None)
        if simulator:
            for agent in (simulator.user_agent, simulator.assistant_agent):
                agent.dynamic_memory.retriever.reset_collection()
        return {"status": "deleted"}
