"""Same-origin local workbench; serves only dist assets and research APIs."""
import json
import mimetypes
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from pathlib import Path


class ConsumerResearchAPI:
    def __init__(self, service):
        self.service = service

    def dispatch(self, method, path, body=None, query=None):
        body, query = body or {}, query or {}
        if not isinstance(body, dict):
            raise ValueError("请求体必须为JSON对象")
        if method == "GET" and path == "/api/research/status":
            return self.service.status()
        if method == "POST" and path == "/api/research/sessions":
            return self.service.create_session(consent=body.get("consent", False), purpose=body.get("purpose", "user_research"))
        if method == "POST" and path == "/api/research/nearby":
            return self.service.nearby(longitude=body.get("longitude"), latitude=body.get("latitude"),
                radius=body.get("radius", 2000), category=body.get("category", "restaurant"))
        sid = body.get("session_id") or query.get("session_id")
        if not isinstance(sid, str) or not sid:
            raise ValueError("缺少研究会话编号")
        if method == "GET" and path == "/api/research/session":
            return self.service.get_session(sid)
        if method == "GET" and path == "/api/research/export":
            return self.service.export(sid)
        if method == "POST" and path == "/api/research/chat":
            return self.service.chat(sid, body.get("text"), request_id=body.get("request_id"),
                nearby_search_id=body.get("nearby_search_id"))
        if method == "POST" and path == "/api/research/profile":
            return self.service.confirm_profile(sid, proposal_ids=body.get("proposal_ids"),
                updates=body.get("updates"), scope=body.get("scope", "profile"))
        if method == "POST" and path == "/api/research/context":
            return self.service.set_context(sid, body.get("context"))
        if method == "POST" and path == "/api/research/reconnect-memory":
            return self.service.reconnect_memory(sid)
        if method == "POST" and path == "/api/research/runs":
            return self.service.rehearse(sid, branch_type=body.get("branch_type", "baseline"),
                request_id=body.get("request_id"), candidates=body.get("candidates"))
        if method == "POST" and path == "/api/research/feedback":
            return self.service.feedback(sid, body.get("run_id"), selected_id=body.get("selected_id"),
                satisfaction=body.get("satisfaction"), comment=body.get("comment", ""), skip=body.get("skip", False))
        if method == "DELETE" and path == "/api/research/session":
            return self.service.delete(sid)
        raise KeyError("研究接口不存在")


def create_workbench_server(service, static_directory, *, host="127.0.0.1", port=5173):
    api = ConsumerResearchAPI(service)
    static_root = Path(static_directory).resolve()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, status, value):
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _request(self):
            parsed = urlparse(self.path)
            if self.command == "POST" and parsed.path == "/api/research/chat/stream":
                self._stream_chat()
                return
            if parsed.path.startswith("/api/"):
                try:
                    allowed = {f"http://localhost:{self.server.server_port}", f"http://127.0.0.1:{self.server.server_port}"}
                    origin = self.headers.get("Origin")
                    if origin and origin not in allowed:
                        raise PermissionError("只接受本地同源工作台请求")
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 <= length <= 200_000:
                        raise ValueError("请求体过大")
                    if self.command != "GET" and not self.headers.get("Content-Type", "").startswith("application/json"):
                        raise ValueError("请求必须使用JSON")
                    body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    self._json(200, api.dispatch(self.command, parsed.path, body, query))
                except PermissionError as error:
                    self._json(403, {"error": str(error)})
                except KeyError:
                    self._json(404, {"error": "研究会话、记录或接口不存在"})
                except (TypeError, ValueError) as error:
                    self._json(400, {"error": str(error)})
                except RuntimeError as error:
                    self._json(503, {"error": str(error)})
                except Exception:
                    self._json(503, {"error": "LifeSim研究服务暂不可用，请检查本机配置与模型网络"})
                return
            if self.command != "GET":
                self._json(405, {"error": "静态文件仅允许读取"})
                return
            target = (static_root / unquote(parsed.path).lstrip("/")).resolve()
            if parsed.path == "/":
                target = static_root / "index.html"
            if not target.is_relative_to(static_root) or not target.is_file() or any(p.startswith(".") for p in target.relative_to(static_root).parts):
                self._json(404, {"error": "文件不存在"})
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", (mimetypes.guess_type(str(target))[0] or "application/octet-stream") + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _stream_chat(self):
            try:
                allowed = {f"http://localhost:{self.server.server_port}", f"http://127.0.0.1:{self.server.server_port}"}
                origin = self.headers.get("Origin")
                if origin and origin not in allowed:
                    raise PermissionError("只接受本地同源工作台请求")
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 200_000 or not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("请求必须为有效JSON")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("请求体必须为JSON对象")
            except (PermissionError, ValueError, TypeError) as error:
                self._json(403 if isinstance(error, PermissionError) else 400, {"error": str(error)})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def emit(kind, value):
                try:
                    self.wfile.write((json.dumps({"type": kind, "value": value}, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass  # The model can still finish and save an idempotent request.

            try:
                result = self.server.research_api.service.chat(body.get("session_id"), body.get("text"),
                    request_id=body.get("request_id"), nearby_search_id=body.get("nearby_search_id"),
                    progress=lambda phase: emit("phase", phase),
                    on_delta=lambda delta: emit("delta", delta))
                emit("session", result)
            except (ValueError, TypeError, RuntimeError) as error:
                emit("error", str(error))
            except KeyError:
                emit("error", "研究会话不存在")
            except Exception:
                emit("error", "研究服务暂不可用，请重试本轮消息")

        do_GET = do_POST = do_DELETE = _request
    class WorkbenchServer(HTTPServer):
        allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
    server = WorkbenchServer((host, port), Handler)
    server.research_api = api
    return server
