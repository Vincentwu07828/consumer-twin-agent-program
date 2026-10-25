"""Opt-in live API smoke check. Scripted probe inputs are not empirical participant data."""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4
import _bootstrap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    sid = None
    def api(path, body=None):
        encoded = json.dumps(body, ensure_ascii=False).encode("utf8") if body is not None else None
        request = Request(args.base_url + path, data=encoded, headers={"Content-Type":"application/json"})
        try:
            with urlopen(request, timeout=240) as response:
                return json.load(response)
        except HTTPError as error:
            data = json.load(error)
            raise RuntimeError(data.get("error", "API request failed")) from None
    try:
        status = api("/api/research/status")
        print(json.dumps({"step":"configuration", "status":status["status"], "model":status["model"]}), flush=True)
        session = api("/api/research/sessions", {"consent":True,"purpose":"integration_probe"})
        sid = session["id"]
        session = api("/api/research/chat", {"session_id":sid,"request_id":str(uuid4()),
            "text":"通常每餐预算50元，喜欢面食，不吃牛肉。我比较看重距离，愿意为了少走路适当多花一点。"})
        print(json.dumps({"step":"real_llm_chat", "proposal_count":len(session["pending_facts"]),
            "reply_chars":len(session["messages"][-1]["text"])}), flush=True)
        session = api("/api/research/profile", {"session_id":sid,"proposal_ids":[p["id"] for p in session["pending_facts"]]})
        if session["profile"]["budget"] is None:
            raise RuntimeError("Live extraction did not propose an explicit budget")
        session = api("/api/research/chat", {"session_id":sid,"request_id":str(uuid4()),
            "text":"请根据我确认的画像，继续问一个能了解我消费取舍的问题。"})
        retrieved = session["messages"][-1].get("retrieved_memories", [])
        print(json.dumps({"step":"native_memory_retrieval", "count":len(retrieved)}), flush=True)
        if not retrieved:
            raise RuntimeError("Native memory retrieval returned no confirmed evidence")
        session = api("/api/research/runs", {"session_id":sid,"request_id":str(uuid4())})
        run = session["runs"][-1]
        assert "prediction" not in run and "simulation_dialogue" not in run
        session = api("/api/research/feedback", {"session_id":sid,"run_id":run["id"],
            "selected_id":"demo-green","satisfaction":4,"comment":"集成检查脚本反馈：本次想清爽一点，也在意距离。"})
        revealed = session["runs"][-1]
        assert revealed["observed_choice"]["source"] == "integration_probe_choice"
        assert revealed["prediction"]["source"] == "lifesim_deepseek_simulation"
        exported = api("/api/research/export?session_id=" + sid)
        path = _bootstrap.ROOT / "reports/generated/lifesim_deepseek_live_probe.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({"step":"live_rehearsal_and_feedback", "session_id":sid,
            "model":revealed["model_version"], "simulation_turns":len(revealed["simulation_dialogue"]),
            "choice_id":revealed["prediction"]["choice_id"], "report":str(path)}, ensure_ascii=True), flush=True)
    except Exception as error:
        print(json.dumps({"status":"failed", "session_id":sid,"error":str(error)},ensure_ascii=True),flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
