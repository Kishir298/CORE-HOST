"""R.E.S.C.S. retrieval seam for the C.O.R.E. data layer.

C.O.R.E. never touches R.E.S.C.S. internals. All reads go through
:class:`RescsDataReader`, which exposes exactly the five logical
operations the data organizer needs. Owner scoping is structural: a
reader is bound to one ``owner_scope`` at construction and refuses
cross-owner reads unless explicitly permitted, so C.O.R.E. cannot
retrieve-then-filter another owner's data.

Two backends:

- :class:`AdapterDataReader` — reads through the existing
  ``RescsAdapter`` (``fetch_resource`` / ``list_resources``). Records are
  projected from ``Resource`` objects (``namespace`` from
  ``metadata["namespace"]``, default ``core.resources``). Files are not
  supported by the adapter contract and report unavailable.
- :class:`HttpDataReader` — reads through the R.E.S.C.S. HTTP data
  contract (stdlib ``urllib`` only, fixed timeout, zero retries).

All failures surface as :class:`DataError` subclasses only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from core.communication.protocol import RESCS_REQUEST_TIMEOUT

from .errors import (
    DataAccessDenied,
    DataNotFound,
    DataRetrievalFailed,
    DataSourceUnavailable,
)

DEFAULT_NAMESPACE = "core.resources"


class RescsDataReader(ABC):
    """Owner-scoped read interface over R.E.S.C.S. data."""

    def __init__(
        self,
        owner_scope: str | None,
        allow_cross_owner: bool = False,
    ) -> None:
        self._owner_scope = owner_scope
        self._allow_cross_owner = bool(allow_cross_owner)

    @property
    def owner_scope(self) -> str | None:
        """Return the owner this reader is scoped to."""
        return self._owner_scope

    def _check_owner(self, owner: str | None) -> str | None:
        """Enforce owner scoping. Returns the effective owner filter."""
        effective = owner if owner is not None else self._owner_scope
        if (
            not self._allow_cross_owner
            and self._owner_scope is not None
            and effective != self._owner_scope
        ):
            raise DataAccessDenied(
                f"Access denied for owner: {owner!r}."
            )
        return effective

    @abstractmethod
    def get_record(
        self, namespace: str, key: str, owner: str | None = None
    ) -> dict[str, Any]:
        """Return one raw record dict, or raise a DataError."""

    @abstractmethod
    def list_records(
        self,
        namespace: str | None = None,
        key_prefix: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return raw record dicts (unsorted, unpaginated)."""

    @abstractmethod
    def search_records(
        self,
        query: str,
        namespace: str | None = None,
        key_prefix: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return raw record dicts matching a substring query."""

    @abstractmethod
    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        """Return one raw file-metadata dict, or raise a DataError."""

    @abstractmethod
    def get_file_bytes(self, file_id: str) -> tuple[dict[str, Any], bytes]:
        """Return ``(metadata, content_bytes)``, or raise a DataError."""


def _resource_to_record(resource: Any) -> dict[str, Any]:
    """Project a ``Resource`` into a raw record dict (no storage access)."""
    metadata = dict(getattr(resource, "metadata", {}) or {})
    namespace = metadata.get("namespace", DEFAULT_NAMESPACE)
    return {
        "id": getattr(resource, "resource_id", ""),
        "namespace": namespace,
        "key": metadata.get("key", getattr(resource, "resource_id", "")),
        "value": {
            "name": getattr(resource, "name", ""),
            "status": getattr(resource, "status", ""),
            "capabilities": list(getattr(resource, "capabilities", []) or []),
            "metadata": metadata,
        },
        "metadata": metadata,
        "owner": getattr(resource, "owner", None),
        "version": metadata.get("version"),
        "etag": metadata.get("etag"),
        "created_at": metadata.get("created_at"),
        "updated_at": metadata.get("updated_at"),
    }


class AdapterDataReader(RescsDataReader):
    """Read records through an existing ``RescsAdapter`` instance."""

    def __init__(
        self,
        adapter: Any,
        owner_scope: str | None,
        allow_cross_owner: bool = False,
    ) -> None:
        super().__init__(owner_scope, allow_cross_owner)
        self._adapter = adapter

    def _all_records(self) -> list[dict[str, Any]]:
        try:
            resources = self._adapter.list_resources()
        except Exception as exc:
            raise DataSourceUnavailable(
                "R.E.S.C.S. adapter is unavailable."
            ) from exc
        return [_resource_to_record(r) for r in resources or []]

    def get_record(
        self, namespace: str, key: str, owner: str | None = None
    ) -> dict[str, Any]:
        effective_owner = self._check_owner(owner)
        for record in self._all_records():
            if record["namespace"] != namespace or record["key"] != key:
                continue
            if effective_owner is not None and record["owner"] != effective_owner:
                continue
            return record
        raise DataNotFound(f"Record was not found: {namespace}/{key}.")

    def list_records(
        self,
        namespace: str | None = None,
        key_prefix: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        effective_owner = self._check_owner(owner)
        result = []
        for record in self._all_records():
            if namespace is not None and record["namespace"] != namespace:
                continue
            if key_prefix is not None and not str(record["key"]).startswith(key_prefix):
                continue
            if effective_owner is not None and record["owner"] != effective_owner:
                continue
            result.append(record)
        return result

    def search_records(
        self,
        query: str,
        namespace: str | None = None,
        key_prefix: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = (query or "").lower()
        result = []
        for record in self.list_records(namespace, key_prefix, owner):
            haystacks = [
                str(record.get("key", "")),
                json.dumps(record.get("value", {}), default=str),
                str((record.get("metadata", {}) or {}).get("name", "")),
            ]
            if any(needle in hay.lower() for hay in haystacks):
                result.append(record)
        return result

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        raise DataSourceUnavailable(
            "File storage is not available on this R.E.S.C.S. adapter."
        )

    def get_file_bytes(self, file_id: str) -> tuple[dict[str, Any], bytes]:
        raise DataSourceUnavailable(
            "File storage is not available on this R.E.S.C.S. adapter."
        )


class HttpDataReader(RescsDataReader):
    """Read records/files through the R.E.S.C.S. HTTP data contract.

    Fixed timeout, zero retries. Only ``DataError`` subclasses escape.
    """

    def __init__(
        self,
        endpoint: str,
        owner_scope: str | None,
        allow_cross_owner: bool = False,
        timeout: float = RESCS_REQUEST_TIMEOUT,
    ) -> None:
        super().__init__(owner_scope, allow_cross_owner)
        self._endpoint = (endpoint or "").rstrip("/")
        self._timeout = float(timeout) if timeout else RESCS_REQUEST_TIMEOUT

    def _url(self, path: str, params: dict | None = None) -> str:
        url = f"{self._endpoint}{path}"
        if params:
            query = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
            if query:
                url = f"{url}?{query}"
        return url

    def _get_json(self, path: str, params: dict | None = None) -> Any:
        try:
            with urllib.request.urlopen(
                self._url(path, params), timeout=self._timeout
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise DataNotFound("Requested data was not found.")
            if exc.code in (401, 403):
                raise DataAccessDenied("Access denied by R.E.S.C.S..")
            raise DataRetrievalFailed("R.E.S.C.S. request failed.")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DataSourceUnavailable(
                "R.E.S.C.S. service is unavailable."
            ) from exc
        except Exception as exc:
            raise DataRetrievalFailed("R.E.S.C.S. request failed.") from exc
        try:
            return json.loads(body) if body else None
        except Exception as exc:
            raise DataRetrievalFailed("Invalid R.E.S.C.S. response.") from exc

    def get_record(
        self, namespace: str, key: str, owner: str | None = None
    ) -> dict[str, Any]:
        effective_owner = self._check_owner(owner)
        data = self._get_json(
            f"/records/{urllib.parse.quote(namespace)}/{urllib.parse.quote(key)}",
            {"owner": effective_owner},
        )
        record = (data or {}).get("record", data)
        if not isinstance(record, dict) or not record.get("id"):
            raise DataNotFound(f"Record was not found: {namespace}/{key}.")
        return record

    def _collection(self, path: str, params: dict) -> list[dict]:
        data = self._get_json(path, params)
        if isinstance(data, dict):
            for key in ("records", "items", "data"):
                if isinstance(data.get(key), list):
                    return [r for r in data[key] if isinstance(r, dict)]
            return []
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        return []

    def list_records(
        self,
        namespace: str | None = None,
        key_prefix: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        effective_owner = self._check_owner(owner)
        return self._collection(
            "/records",
            {
                "namespace": namespace,
                "key_prefix": key_prefix,
                "owner": effective_owner,
            },
        )

    def search_records(
        self,
        query: str,
        namespace: str | None = None,
        key_prefix: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        effective_owner = self._check_owner(owner)
        return self._collection(
            "/records/search",
            {
                "query": query,
                "namespace": namespace,
                "key_prefix": key_prefix,
                "owner": effective_owner,
            },
        )

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        data = self._get_json(f"/files/{urllib.parse.quote(file_id)}/metadata")
        meta = (data or {}).get("file", data)
        if not isinstance(meta, dict) or not meta.get("id"):
            raise DataNotFound("File was not found.")
        return meta

    def get_file_bytes(self, file_id: str) -> tuple[dict[str, Any], bytes]:
        import base64

        data = self._get_json(f"/files/{urllib.parse.quote(file_id)}/content")
        if not isinstance(data, dict):
            raise DataRetrievalFailed("Invalid file content response.")
        meta = data.get("file", {})
        content_b64 = data.get("content_base64")
        if not isinstance(meta, dict) or not isinstance(content_b64, str):
            raise DataRetrievalFailed("Invalid file content response.")
        try:
            content = base64.b64decode(content_b64, validate=True)
        except Exception as exc:
            raise DataRetrievalFailed("Invalid file content encoding.") from exc
        return meta, content


__all__ = [
    "RescsDataReader",
    "AdapterDataReader",
    "HttpDataReader",
    "DEFAULT_NAMESPACE",
]
