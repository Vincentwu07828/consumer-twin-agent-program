"""Local REST adapter; frontend is intentionally a later task."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


class RestaurantAPI:
    def __init__(self, service):
        self.service = service

    def dispatch(self, method, path, body=None, query=None):
        body, query = body or {}, query or {}
        if not isinstance(body, dict):
            raise ValueError("请求体必须为 JSON 对象")
        if method == "GET" and path == "/api/health":
            return {"status": "ok", "product": "restaurant_recommendation", "training_required": False}
        if method == "GET" and path == "/api/questionnaire":
            return self.service.questionnaire()
        if method == "POST" and path == "/api/sessions":
            return self.service.create_session(body.get("answers", {}), body.get("consent", False))
        session_id = body.get("session_id") or query.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("缺少 session_id")
        if method == "GET" and path == "/api/profile":
            return {"profile": self.service.profile(session_id)}
        if method == "POST" and path == "/api/intent":
            return self.service.check_intent(session_id, now=body.get("now"),
                explicit_hunger=body.get("explicit_hunger"), last_meal_at=body.get("last_meal_at"))
        if method == "POST" and path == "/api/recommendations":
            return self.service.recommend(session_id, need=body.get("need", ""), context=body.get("context"),
                candidates=body.get("candidates"), explicit_hunger=body.get("explicit_hunger"), last_meal_at=body.get("last_meal_at"))
        if method == "POST" and path == "/api/feedback":
            if not isinstance(body.get("run_id"), str):
                raise ValueError("缺少 run_id")
            return self.service.feedback(session_id, body["run_id"], clicked=body.get("clicked"),
                selected_id=body.get("selected_id"), satisfaction=body.get("satisfaction"),
                comment=body.get("comment", ""), rejected_reasons=body.get("rejected_reasons"))
        if method == "DELETE" and path == "/api/session":
            return self.service.clear_session(session_id)
        raise KeyError("接口不存在")


def create_http_server(service, host="127.0.0.1", port=8000):
    api = RestaurantAPI(service)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Requests may contain session identifiers; do not log them.

        def _reply(self, status, payload):
            content = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            origin = self.headers.get("Origin")
            if origin in ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(content)

        def _request(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= 1_000_000:
                    raise ValueError("请求体过大")
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                parsed = urlparse(self.path)
                query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                self._reply(200, api.dispatch(self.command, parsed.path, body, query))
            except PermissionError as error:
                self._reply(403, {"error": str(error)})
            except KeyError:
                self._reply(404, {"error": "会话、记录或接口不存在"})
            except (TypeError, ValueError) as error:
                self._reply(400, {"error": str(error)})
            except Exception:
                self._reply(503, {"error": "推荐服务暂不可用，请检查服务配置与网络"})

        do_GET = do_POST = do_DELETE = _request

        def do_OPTIONS(self):
            self.send_response(204)
            origin = self.headers.get("Origin")
            if origin in ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
    return HTTPServer((host, port), Handler)
