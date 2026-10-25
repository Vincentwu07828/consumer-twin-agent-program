"""Consumer extensions of the actual LifeSim agents; original persona machinery is retained."""
import json

from agents.user_agent import UserAgent
from agents.assistant_agent import AssistantAgent
from utils.utils import parse_json_dict_response


CONSUMER_RULES = """
消费者任务补充要求：使用中文。未知画像属性保持未知，不编造收入、身份或真实购买事实。
餐厅名称、价格、营业、距离及食材信息属于工具结果，不能编造或凭对话修改。
先满足预算和饮食禁忌，再结合口味、距离与历史反馈排序；缺失信息须保持未知。
推荐排序不是点击或购买概率，不替用户选择或订餐。
推断的偏好和解释属于 LLM 推断，不能写成用户确认的事实。
"""


class ConsumerUserAgent(UserAgent):
    def _build_chat_system_prompt(self):
        return super()._build_chat_system_prompt().replace("Reply in English", "Reply in Chinese") + CONSUMER_RULES

    def explain_consumer_action(self, evidence):
        prompt = {"task": "结合画像、记忆和当前情境解释已采样的模拟点击；不能改变动作，也不声称真实动机。",
                  "profile": self.get_profile(), "conversation": self.conversations.get(),
                  "evidence": evidence,
                  "output": {"explanation": "中文模型解释"}}
        with self.synchronized():
            response = self.model.chat([{"role": "system", "content": CONSUMER_RULES},
                                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}])
        parsed = parse_json_dict_response(response, ["explanation"])
        if not isinstance(parsed.get("explanation"), str) or not parsed["explanation"].strip():
            raise ValueError("LLM 未返回有效的动作解释")
        return parsed["explanation"]

    def predict_choice(self, evidence):
        """Consumer choice extends native persona/dialogue; it is not a trained click score."""
        prompt = {"task": "扮演这个消费者，依据原生画像、已有记忆与刚才的模拟交流，预演他在本次候选中会选什么。不是替助手挑选最理性的推荐。",
            "profile": self.get_profile(), "conversation": self.conversations.get(), "evidence": evidence,
            "requirements": "只用 allowed_ids 或 none；允许即使有可行候选也暂不消费。选择餐厅时 ranking 第一项必须等于 choice_id；排序不等于概率。事实未知保持未知，不编造真人动机。理由用中文。只输出 JSON。",
            "output": {"choice_id": "候选编号或none", "ranking": [], "reason": "模型模拟理由"}}
        with self.synchronized():
            response = self.model.chat([{"role": "system", "content": self.system_prompt},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}])
        return parse_json_dict_response(response, ["choice_id", "ranking", "reason"])


class ConsumerAssistantAgent(AssistantAgent):
    def _build_system_prompt(self, event):
        prompt = super()._build_system_prompt(event).replace("* Reply in English.", "* Reply in Chinese.")
        self.system_prompt = prompt + CONSUMER_RULES
        return self.system_prompt

    def plan_consumer(self, evidence):
        prompt = {"task": "根据用户明确需求、LifeSim 画像和检索记忆、已通过硬条件的餐厅候选，输出个性化推荐顺序与理由。",
                  "profile": self.user_preferences_str, "conversation": self.conversations.get(),
                  "evidence": evidence,
                  "requirements": "只使用 allowed_ids 中的编号；最多 top_k 个，不必选择最低价；不要代替用户点击或购买。每个编号必须有中文理由。",
                  "output": {"recommended_ids": ["候选编号"], "reasons": {"候选编号": "模型解释"}}}
        response = self.model.chat([{"role": "system", "content": self.system_prompt},
                                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}])
        return parse_json_dict_response(response, ["recommended_ids", "reasons"])

    def extract_research_facts(self, text, known, context):
        prompt = {"task": "从当前真人这句话提取明确偏好和临时状态，供用户确认。只抽取本句话明确表达的内容。",
            "message": text, "confirmed_profile": known, "current_context": context,
            "fields": {"budget": "非负数字，人民币每人一餐", "cuisines": "口味字符串列表",
                "excluded": "不吃食材字符串列表", "allergies": "明确过敏原字符串列表",
                "diet_tags": "必须满足的饮食要求列表", "max_distance": "非负米数", "dinner_time": "HH:MM"},
            "requirements": "只输出 JSON。没有证据输出空列表；quote 必须是 message 的逐字子串。scope=profile 表示长期；今天/这次/今晚必须用 context。餐厅报价不能当用户预算，喜欢安静等可作为 notes 但不得猜测收入/性格。未知不要填空数组，只有明确没有忌口才可[]。相互冲突的表达先提问而非确定抽取。",
            "output": {"facts": [{"field": "budget", "value": 50, "quote": "预算50元", "scope": "profile"}],
                "notes": [{"quote": "喜欢安静的地方", "scope": "profile"}]}}
        response = self.model.chat([{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}])
        return parse_json_dict_response(response, ["facts", "notes"])
