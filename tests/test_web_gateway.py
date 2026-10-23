"""Portal behavior tested without OS TCP listeners."""
import io
import json
from email.message import Message
import sys
import time
from unittest.mock import patch

import pytest

from core.web.accounts import Accounts
from core.web.gateway import Gateway
from core.web.services import Services
from core.web.storage import RescsStorage

PASSWORD = "test-password-long-enough"


@pytest.fixture()
def gateway(tmp_path):
    app = Gateway(directory=tmp_path)
    yield app
    app.close()


def login(gateway, username="owner"):
    if gateway.accounts.empty(): gateway.accounts.create(username, PASSWORD, "Owner", first=True)
    status, body, cookie = gateway.dispatch("POST", "/api/login", {"username":username,"password":PASSWORD}, {})
    assert status == 200
    return {"Cookie": cookie.split(";",1)[0], "X-Core-CSRF":body["csrf"]}, body["profile"]


def test_setup_requires_host_key_and_cannot_repeat(gateway):
    body = {"username":"owner", "password":PASSWORD,"name":"Owner", "setup_key":"wrong"}
    with pytest.raises(PermissionError): gateway.dispatch("POST","/api/setup",body,{})
    body["setup_key"] = gateway.setup_key
    assert gateway.dispatch("POST","/api/setup",body,{})[0] == 201
    with pytest.raises(PermissionError): gateway.dispatch("POST","/api/setup",body,{})


def test_credentials_are_hashed_and_sessions_can_be_revoked(gateway):
    headers, user = login(gateway)
    raw = gateway.accounts.path.read_bytes()
    assert PASSWORD.encode() not in raw
    assert gateway.dispatch("GET","/api/me",{},headers)[1]["profile"]["id"] == user["id"]
    gateway.dispatch("POST","/api/logout",{},headers)
    with pytest.raises(PermissionError): gateway.dispatch("GET","/api/me",{},headers)


def test_csrf_and_member_admin_boundaries(gateway):
    owner, _ = login(gateway)
    gateway.dispatch("POST","/api/profiles",{"username":"member","password":PASSWORD,"name":"Member"},owner)
    member, _ = login(gateway,"member")
    for method,route,body in [("GET","/api/settings",{}),("GET","/api/profiles",{}),("POST","/api/jobs",{"service":"ascs","prompt":"hello"})]:
        with pytest.raises(PermissionError): gateway.dispatch(method,route,body,member)
    with pytest.raises(PermissionError): gateway.dispatch("POST","/api/logout",{}, {"Cookie":owner["Cookie"]})


def test_storage_scope_comes_from_session(gateway):
    headers, user = login(gateway)
    with patch.object(gateway.storage,"save",return_value={"id":"stored"}) as save:
        gateway.dispatch("POST","/api/records",{"key":"notes","value":{"text":"private"},"owner":"foreign"},headers)
    assert save.call_args.args[0] == user["id"]
    assert "foreign" not in save.call_args.args


def test_foreign_backend_records_fail_closed():
    storage = RescsStorage("http://127.0.0.1:8081", "a"*32)
    with patch.object(storage,"request",return_value={"items":[{"owner":"foreign","namespace":"private"}]}):
        with pytest.raises(PermissionError): storage.records("a"*32)


def test_real_rescs_record_contract_is_used():
    storage = RescsStorage("http://127.0.0.1:8081", "a"*32)
    with patch.object(storage,"request",return_value={"id":"r"}) as request:
        storage.save("a"*32,"notes",{"text":"hello"},"asis")
    assert request.call_args.args == ("PUT","/api/v1/records")
    assert request.call_args.kwargs["body"] == {"owner":"core.profile."+"a"*32,
        "namespace":"personal.profile."+"a"*32+".asis", "key":"notes", "value":{"text":"hello"}}


@pytest.mark.parametrize("service", ["../other","..","x/y", "a"*33])
def test_scope_paths_cannot_be_injected(service):
    with pytest.raises(ValueError): RescsStorage.scope("a"*32,service)


def test_secure_cookie_for_public_origin(gateway):
    gateway.accounts.create("owner",PASSWORD,"Owner",first=True)
    gateway.config["public_origin"] = "https://core.example.com"
    cookie = gateway.dispatch("POST","/api/login",{"username":"owner","password":PASSWORD},{"Host":"core.example.com"})[2]
    assert "; Secure" in cookie and "; HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_host_and_origin_guards(gateway):
    with pytest.raises(PermissionError): gateway.check_request("GET",{"Host":"evil.example:8780"})
    with pytest.raises(PermissionError): gateway.check_request("POST",{"Host":"127.0.0.1:8780","Origin":"http://localhost:9000","Content-Type":"application/json"})
    gateway.check_request("POST",{"Host":"127.0.0.1:8780","Origin":"http://127.0.0.1:8780","Content-Type":"application/json"})


def test_login_attempt_window_is_bounded(gateway):
    for _ in range(5):
        with pytest.raises(PermissionError): gateway.dispatch("POST","/api/login",{"username":"no-user","password":PASSWORD},{})
    with pytest.raises(PermissionError,match="Too many"): gateway.dispatch("POST","/api/login",{}, {})


def test_settings_never_return_backend_secret(gateway):
    headers, _ = login(gateway)
    data = gateway.dispatch("GET","/api/settings",{},headers)[1]
    assert "rescs_key" not in data
    assert gateway.config["rescs_key"] not in json.dumps(data)


def test_job_ownership_is_enforced(tmp_path):
    from core.web.services import Job
    services = Services(tmp_path,{},"private")
    services.jobs["j"] = Job("j","alice","asis")
    with pytest.raises(PermissionError): services.get("j",{"id":"bob","role":"owner"})
    assert services.get("j",{"id":"alice"}).id == "j"


def test_service_argv_has_no_shell_and_profile_directories(tmp_path):
    root = tmp_path / "ASIS"; (root/"asis").mkdir(parents=True)
    services = Services(tmp_path,{"asis":str(root)},"private")
    prompt = "--help; arbitrary shell text"
    argv, cwd, env = services.command("asis",{"id":"a"*32,"role":"member"},prompt,"plan")
    assert argv[-1] == "--message="+prompt
    assert str(tmp_path/"profiles"/('a'*32)) in env["ASIS_DATA_DIRECTORY"]
    assert "RESCS_API_KEY" not in env and cwd == root


def test_tiviss_uses_real_provider_and_profile_memory(tmp_path):
    root=tmp_path/"TIVISS"; (root/"tiviss").mkdir(parents=True)
    services=Services(tmp_path,{"tiviss":str(root)},"private",{"tiviss":"installed-model:tag"})
    argv, _, env=services.command("tiviss",{"id":"a"*32,"role":"owner"},"hello","plan")
    assert argv[-1]=="--message=hello" and env["TIVISS_MODEL_PROVIDER"]=="ollama"
    assert "a"*32 in env["TIVISS_MEMORY_CONVERSATION_DB"]


def test_real_process_output_streams_and_exits(tmp_path):
    services = Services(tmp_path,{},"private")
    with patch.object(services,"command",return_value=([sys.executable,"-u","-c","print('hello from process')"],tmp_path,dict(__import__('os').environ))):
        result = services.start("asis",{"id":"owner","role":"owner"},"hello")
    job = services.get(result["id"],{"id":"owner"})
    deadline = time.time()+5
    while job.status in ("starting","running") and time.time()<deadline: time.sleep(.02)
    assert job.status == "completed"
    assert "hello from process" in "".join(e["text"] for e in job.view()["events"])
    services.close()


def test_http_handler_serves_portal_and_requires_auth(gateway):
    cls = gateway.handler()
    def request(path):
        handler = cls.__new__(cls)
        handler.path, handler.command = path, "GET"
        handler.requestline, handler.request_version = "GET "+path+" HTTP/1.1", "HTTP/1.1"
        handler.headers = Message(); handler.headers['Host'] = '127.0.0.1:8780'
        handler.wfile, handler.rfile = io.BytesIO(), io.BytesIO()
        handler.client_address = ('127.0.0.1',12345)
        handler.do_GET()
        return handler.wfile.getvalue()
    assert b'C.O.R.E.' in request('/')
    assert b'403' in request('/api/me').split(b'\r\n')[0]


def test_account_restart_retains_profiles(tmp_path):
    accounts = Accounts(tmp_path)
    user = accounts.create("owner",PASSWORD,"Owner",first=True)
    accounts.close()
    reopened = Accounts(tmp_path)
    assert reopened.login("owner",PASSWORD)[2]["id"] == user["id"]
    reopened.close()
