"""Local HTTP protocol probe; no external API or model download is used."""
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error
import _bootstrap


def main():
    process = subprocess.Popen([sys.executable, str(_bootstrap.ROOT / "scripts/serve_restaurant_agent.py"), "--port", "8766"],
        cwd=_bootstrap.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8766/api/health", timeout=1) as response:
                    health = json.load(response)
                break
            except urllib.error.URLError:
                if process.poll() is not None:
                    raise RuntimeError("本地后端启动失败")
                time.sleep(.25)
        else:
            raise RuntimeError("本地后端没有按时启动")
        assert health["training_required"] is False
        with urllib.request.urlopen("http://127.0.0.1:8766/api/questionnaire", timeout=2) as response:
            assert len(json.load(response)["questions"]) == 7
        data = json.dumps({"consent": True, "answers": {"preferred_cuisines": ["川菜"], "meal_budget_cny": 50}}).encode()
        request = urllib.request.Request("http://127.0.0.1:8766/api/sessions", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=2) as response:
            session = json.load(response)
        data = json.dumps({"session_id": session["session_id"], "now": "2026-10-14T18:00:00+08:00"}).encode()
        request = urllib.request.Request("http://127.0.0.1:8766/api/intent", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=2) as response:
            assert json.load(response)["requires_confirmation"] is True
        data = json.dumps({"session_id": session["session_id"]}).encode()
        request = urllib.request.Request("http://127.0.0.1:8766/api/session", method="DELETE", data=data,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=2) as response:
            assert json.load(response)["status"] == "deleted"
        print("restaurant_http_contract_ok")
    finally:
        process.terminate()
        process.communicate(timeout=10)


if __name__ == "__main__":
    main()
