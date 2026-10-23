"""Authenticated C.O.R.E. web workspace; backend agents stay independent."""
from __future__ import annotations

from collections import defaultdict, deque
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse

from .accounts import Accounts
from .services import CATALOG, Services
from .storage import RescsStorage

MAX_BODY = 256 * 1024


class Gateway:
    def __init__(self, app=None, *, directory=None, port=8780):
        self.app, self.port = app, int(port)
        self.data = Path(directory or os.environ.get("CORE_WEB_DATA", "var/web")).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data / "gateway.json"
        self.lock = threading.RLock()
        parent = Path(__file__).resolve().parents[3]
        self.config = {"rescs_url": "http://127.0.0.1:8081", "rescs_key": secrets.token_urlsafe(32),
                       "public_origin": "", "roots": {k: str(parent/v[2]) for k,v in CATALOG.items()}, "links": {},
                       "models": {"asis":"", "ascs":"", "tiviss":""}}
        if self.config_path.exists(): self.config.update(json.loads(self.config_path.read_text()))
        self.config["public_origin"] = os.environ.get("CORE_PUBLIC_ORIGIN", self.config["public_origin"]).rstrip("/")
        self.validate_origin(self.config["public_origin"])
        self.save_config()
        self.accounts = Accounts(self.data)
        self.setup_key = secrets.token_urlsafe(32)
        self.storage = RescsStorage(self.config["rescs_url"], self.config["rescs_key"])
        self.services = Services(self.data, self.config["roots"], self.config["rescs_key"], self.config["models"])
        self.attempts = defaultdict(deque)

    @staticmethod
    def validate_origin(origin):
        if not origin: return
        p = urlparse(origin)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.path or p.query or p.fragment:
            raise ValueError("The public origin must be an HTTPS origin without a path.")

    def save_config(self):
        import tempfile
        with self.lock:
            fd, path = tempfile.mkstemp(prefix="gateway-", suffix=".json", dir=self.data)
            with os.fdopen(fd, "w") as file: json.dump(self.config, file, indent=2)
            Path(path).replace(self.config_path)

    @staticmethod
    def token(headers):
        cookie = SimpleCookie()
        try: cookie.load(headers.get("Cookie", ""))
        except Exception: return ""
        return cookie["core_session"].value if "core_session" in cookie else ""

    def cookie(self, token, public=False, expired=False):
        return f"core_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if expired else 43200}" + ("; Secure" if public else "")

    def dispatch(self, method, path, body, headers, remote="127.0.0.1"):
        route = urlparse(path).path
        query = parse_qs(urlparse(path).query)
        token = self.token(headers)
        user = self.accounts.session(token)
        public = headers.get("Host", "") == urlparse(self.config["public_origin"]).netloc
        if method == "GET" and route == "/api/setup": return 200, {"setup_required": self.accounts.empty()}, None
        if method == "POST" and route == "/api/setup":
            if not hmac.compare_digest(str(body.get("setup_key", "")), self.setup_key) or not self.setup_key:
                raise PermissionError("Enter the setup key printed by the host launcher.")
            user = self.accounts.create(body.get("username"), body.get("password"), body.get("name"), first=True)
            self.setup_key = ""
            self.accounts.audit(user["id"], "setup")
            return 201, {"created": True}, None
        if method == "POST" and route == "/api/login":
            with self.lock:
                attempts = self.attempts[remote]
                while attempts and time.time()-attempts[0] > 900: attempts.popleft()
                if len(attempts) >= 5: raise PermissionError("Too many attempts. Try again in 15 minutes.")
                attempts.append(time.time())
            session, csrf, user = self.accounts.login(body.get("username"), body.get("password"))
            with self.lock: self.attempts[remote].clear()
            self.accounts.audit(user["id"], "login")
            return 200, {"profile": user, "csrf": csrf}, self.cookie(session, public)
        if user is None: raise PermissionError("Sign in to C.O.R.E.")
        if method != "GET" and not hmac.compare_digest(str(headers.get("X-Core-CSRF", "")), user["csrf"]):
            raise PermissionError("Session verification failed. Reload the page.")
        if method == "GET" and route == "/api/me":
            return 200, {"profile": {k:v for k,v in user.items() if k != "csrf"}, "csrf": user["csrf"]}, None
        if method == "POST" and route == "/api/logout":
            self.accounts.logout(token)
            return 200, {"signed_out": True}, self.cookie("", public, True)
        if route == "/api/services" and method == "GET": return 200, {"services": self.services.catalog(), "links": self.config["links"]}, None
        if route == "/api/status" and method == "GET":
            return 200, {"host_running": bool(self.app and self.app.is_running), "web": "online",
                         "storage": "RESCS", "storage_owner": self.storage.scope(user["id"])[0]}, None
        if route == "/api/records":
            if method == "GET": return 200, self.storage.records(user["id"], query.get("service", ["workspace"])[0],
                limit=int(query.get("limit", ["100"])[0]), offset=int(query.get("offset", ["0"])[0])), None
            if method == "POST":
                saved = self.storage.save(user["id"], body.get("key"), body.get("value"), body.get("service", "workspace"))
                self.accounts.audit(user["id"], "record_saved")
                return 200, saved, None
        if route == "/api/jobs" and method == "POST":
            if body.get("service") == "rescs" and self.storage.endpoint != "http://127.0.0.1:8081":
                raise ValueError("Managed RESCS uses http://127.0.0.1:8081. Keep the configured backend running independently or explicitly change Settings before managed startup.")
            result = self.services.start(body.get("service"), user, body.get("prompt", ""), body.get("mode", "plan"))
            self.accounts.audit(user["id"], "service_requested", {"service": body.get("service"), "job": result["id"]})
            return 202, result, None
        if route.startswith("/api/jobs/"):
            job_id = route.split("/")[3]
            job = self.services.get(job_id, user)
            if method == "GET": return 200, job.view(int(query.get("after", ["0"])[0])), None
            if method == "POST" and route.endswith("/stop"): return 200, self.services.stop(job_id, user), None
            if method == "POST" and route.endswith("/save"):
                if job.status in ("starting", "running"): raise ValueError("Stop or finish the task first.")
                return 200, self.storage.save(user["id"], "task-"+job.id, job.view(), job.service), None
        if route in ("/api/profiles", "/api/settings", "/api/audit"):
            if user["role"] != "owner": raise PermissionError("Host owner access is required.")
            if route == "/api/profiles":
                if method == "GET": return 200, {"profiles": self.accounts.profiles()}, None
                if method == "POST": return 201, self.accounts.create(body.get("username"), body.get("password"), body.get("name")), None
            if route == "/api/audit" and method == "GET": return 200, {"events": self.accounts.events()}, None
            if route == "/api/settings":
                if method == "GET": return 200, {k:v for k,v in self.config.items() if k != "rescs_key"}, None
                if method == "POST": return self.settings(body, user)
        return 404, {"error": "Unknown endpoint."}, None

    def settings(self, body, user):
        with self.lock:
            candidate = dict(self.config)
            roots, links = body.get("roots", candidate["roots"]), body.get("links", candidate["links"])
            if not isinstance(roots, dict) or set(roots) != set(CATALOG) or not all(isinstance(v,str) and v for v in roots.values()):
                raise ValueError("Provide a source path for every registered service.")
            if not isinstance(links, dict) or any(k not in CATALOG for k in links): raise ValueError("Unknown service link.")
            for url in links.values():
                p = urlparse(url)
                if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
                    raise ValueError("Service links must be HTTP(S) URLs.")
            origin = body.get("public_origin", candidate["public_origin"])
            self.validate_origin(origin)
            models = body.get("models", candidate["models"])
            if not isinstance(models,dict) or set(models) != {"asis","ascs","tiviss"} or not all(isinstance(v,str) and len(v)<200 for v in models.values()):
                raise ValueError("Provide model names for asis, ascs and tiviss (empty retains ASIS/ASCS defaults).")
            key = body.get("rescs_key") or candidate["rescs_key"]
            if not isinstance(key,str) or len(key) < 16: raise ValueError("RESCS keys require at least 16 characters.")
            storage = RescsStorage(body.get("rescs_url", candidate["rescs_url"]), key)
            candidate.update(roots=roots, links=links, models=models, public_origin=origin, rescs_url=storage.endpoint, rescs_key=key)
            self.config = candidate; self.save_config()
            self.storage = storage; self.services.roots = roots; self.services.api_key = key
            self.services.models = models
        self.accounts.audit(user["id"], "settings_changed")
        return 200, {"saved": True}, None

    def check_request(self, method, headers):
        host = headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.config["public_origin"]: allowed.add(urlparse(self.config["public_origin"]).netloc)
        if host not in allowed: raise PermissionError("Unrecognized host. Configure CORE_PUBLIC_ORIGIN for web access.")
        if method != "GET":
            origin = headers.get("Origin")
            expected = self.config["public_origin"] if host == urlparse(self.config["public_origin"]).netloc else "http://"+host
            if origin and origin != expected: raise PermissionError("Cross-origin requests are disabled.")
            if headers.get("Content-Type", "").split(";",1)[0] != "application/json": raise ValueError("JSON content type is required.")

    def handler(self):
        gateway = self
        ui = (Path(__file__).parent / "portal.html").read_text()
        class Handler(BaseHTTPRequestHandler):
            server_version = "COREWeb/0.5"
            def log_message(self, *args): pass
            def setup(self):
                super().setup(); self.connection.settimeout(15)
            def reply(self, status, data, cookie=None, html=False):
                raw = data.encode() if html else json.dumps(data).encode()
                self.send_response(status)
                for key,value in {"Content-Type": "text/html; charset=utf-8" if html else "application/json",
                   "Content-Length": str(len(raw)), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                   "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                   "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
                    self.send_header(key,value)
                if cookie: self.send_header("Set-Cookie", cookie)
                self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass
            def run_request(self):
                try:
                    gateway.check_request(self.command, self.headers)
                    if self.command == "GET" and urlparse(self.path).path == "/":
                        self.reply(200, ui, html=True); return
                    body = {}
                    if self.command != "GET":
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 0 < length <= MAX_BODY: raise ValueError("Request body must be 1–262144 bytes.")
                        body = json.loads(self.rfile.read(length))
                        if not isinstance(body, dict): raise ValueError("Expected a JSON object.")
                    status, data, cookie = gateway.dispatch(self.command, self.path, body, self.headers, self.client_address[0])
                    self.reply(status, data, cookie)
                except PermissionError as exc: self.reply(403, {"error": str(exc)})
                except (ValueError, TypeError, KeyError) as exc: self.reply(400, {"error": str(exc)})
                except ConnectionError as exc: self.reply(503, {"error": str(exc)})
                except Exception: self.reply(500, {"error": "The host could not complete this request."})
            do_GET = run_request
            do_POST = run_request
        return Handler

    def close(self):
        self.services.close(); self.accounts.close()


def serve(app=None, *, directory=None, port=8780, open_browser=False):
    gateway = Gateway(app, directory=directory, port=port)
    server = ThreadingHTTPServer(("127.0.0.1", port), gateway.handler())
    print(f"C.O.R.E. portal: http://127.0.0.1:{port}")
    if gateway.accounts.empty(): print("First-run setup key:", gateway.setup_key)
    print("Keep this window open. Ctrl+C stops managed services.")
    try:
        if app: app.start()
        if open_browser:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{port}")
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); gateway.close()
        if app: app.stop()
