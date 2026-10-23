"""The real RESCS v1 API; profile owner/namespace is derived server-side."""
from __future__ import annotations

import json
import re
import urllib.error
from urllib.parse import urlencode, urlparse
import urllib.request

MAX_BYTES = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OSError("Backend redirects are not permitted.")


class RescsStorage:
    def __init__(self, endpoint, api_key):
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("RESCS endpoint must be an HTTP(S) origin.")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Remote RESCS connections require HTTPS.")
        self.endpoint, self.key = endpoint.rstrip("/"), api_key
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))

    def request(self, method, path, *, body=None, params=None):
        if not self.key: raise ConnectionError("Configure the host RESCS API key in Settings first.")
        url = self.endpoint + path
        if params: url += "?" + urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method,
                     headers={"X-API-Key": self.key, "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=8) as response:
                raw = response.read(MAX_BYTES+1)
                if len(raw) > MAX_BYTES: raise ConnectionError("RESCS response exceeds the portal limit.")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise ConnectionError(f"RESCS returned HTTP {exc.code}.") from exc
        except (OSError, ValueError) as exc:
            raise ConnectionError("RESCS is unavailable or returned an invalid response.") from exc

    @staticmethod
    def scope(user_id, service="workspace"):
        if not re.fullmatch(r"[a-f0-9]{32}", user_id): raise ValueError("Invalid profile ID.")
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", service): raise ValueError("Invalid service scope.")
        return "core.profile." + user_id, "personal.profile." + user_id + "." + service

    def records(self, user_id, service="workspace", *, limit=100, offset=0):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Invalid records pagination.")
        owner, namespace = self.scope(user_id, service)
        page = self.request("GET", "/api/v1/records", params={"owner": owner, "namespace": namespace, "limit": limit, "offset": offset})
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise ConnectionError("Invalid RESCS collection contract.")
        if (any(type(page.get(k)) is not int for k in ("total", "limit", "offset"))
                or page["total"] < 0 or page["limit"] != limit or page["offset"] != offset
                or len(page["items"]) > limit):
            raise ConnectionError("Invalid RESCS pagination contract.")
        # Fail closed if a backend ignores either owner or namespace filtering.
        if any(not isinstance(x, dict) or x.get("owner") != owner or x.get("namespace") != namespace for x in page["items"]):
            raise PermissionError("RESCS returned data outside this profile.")
        return page

    def save(self, user_id, key, value, service="workspace"):
        if not isinstance(key, str) or not 1 <= len(key) <= 200 or not isinstance(value, dict):
            raise ValueError("A record key and JSON object are required.")
        owner, namespace = self.scope(user_id, service)
        saved = self.request("PUT", "/api/v1/records", body={"owner": owner, "namespace": namespace, "key": key, "value": value})
        if not isinstance(saved, dict) or saved.get("owner") != owner or saved.get("namespace") != namespace or saved.get("key") != key:
            raise PermissionError("RESCS returned a saved record outside this profile/key.")
        return saved
