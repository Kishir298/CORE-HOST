# C.O.R.E. Data Organization & R.E.S.C.S. Distribution

The data organization layer retrieves requested data from R.E.S.C.S.,
validates and organizes it, packages it into C.O.R.E. communication
messages, and sends it to authenticated devices through the existing
device communication system.

```text
DEVICE
   |
   | DATA_REQUEST
   v
 C.O.R.E.  (authenticate -> validate -> authorize)
   |
   | R.E.S.C.S. request (owner-scoped)
   v
 R.E.S.C.S.  (authoritative storage)
   |
   | stored data
   v
 C.O.R.E.  (normalize + filter + order + paginate + package + size-check)
   |
   | DATA_RESPONSE via existing device routing
   v
DEVICE
```

C.O.R.E. is the coordinator. R.E.S.C.S. is the authoritative
persistence system. C.O.R.E. stores no records or files.

## Message types

```text
DATA_REQUEST    device -> core, payload carries request_type + filters
DATA_RESPONSE   core -> device, normalized organized data
DATA_ERROR      core -> device, payload {error, message, request_id}
```

`request_id` from the original `DATA_REQUEST` is preserved end to end.
`source` is always `core` on responses; `destination` is the requesting
device, or the explicit `destination_device_id` for distribution.

## Supported request types

| `request_type`   | Required fields        | Response `data_type` |
|------------------|------------------------|----------------------|
| `record_get`     | `namespace`, `key`     | `record` → `{item}`  |
| `record_list`    | — (`namespace`, `key_prefix`, `owner`, `limit`, `offset` optional) | `records` → `{items, total, limit, offset}` |
| `record_search`  | `query` (`namespace`, `key_prefix`, `owner`, `limit`, `offset` optional) | `records` |
| `file_metadata`  | `file_id`              | `file_metadata` → `{item}`, bytes never included |
| `file_download`  | `file_id`              | `file_content` → `{item, content_base64, sha256, size}` if ≤ 1 MiB, else `FILE_TRANSFER_REQUIRED` |

Unknown types → `DATA_ERROR` / `INVALID_DATA_REQUEST`.

## Ordering and pagination

Collections are ordered `updated_at` DESC, `id` ASC (records without a
timestamp keep authoritative order, after timestamped ones, by id).
Pagination defaults `limit=100`, allows `1–500`, `offset >= 0`; invalid
values are rejected with `INVALID_PAGINATION`, never clamped. Page shape:
`{items, total, limit, offset}`.

## Authorization

Every request originates from an authenticated, registered device (`READ`
permission required). Owner scope defaults to the requesting device; the
R.E.S.C.S. read is constructed owner-scoped, never read-all-then-filter.
Cross-owner reads require explicit authorization (`ADMIN`); otherwise
`DATA_ACCESS_DENIED` before any retrieval. Namespaces pass through to the
scoped read; no query mechanism can escape them.

## Distribution

With `destination_device_id`, C.O.R.E. retrieves as the requester, then
routes the packaged `DATA_RESPONSE` to the target through the existing
device routing (existence → registration → online → live binding checks).
Unknown target → `INVALID_DESTINATION`; offline target → 
`DESTINATION_UNAVAILABLE` to the requester (no queue, no blocking, no
retry). Retrieval failures are likewise reported to the requester.

## Error codes

`INVALID_DATA_REQUEST, INVALID_PAGINATION, DATA_NOT_FOUND,
DATA_ACCESS_DENIED, DATA_SOURCE_UNAVAILABLE, DATA_RETRIEVAL_FAILED,
FILE_TRANSFER_REQUIRED, DATA_RESPONSE_TOO_LARGE, INVALID_DESTINATION,
DESTINATION_UNAVAILABLE, COMMUNICATION_ERROR` — plus all pre-existing
device codes. No tracebacks, paths, HTTP details or internals leak.

## Size limits and integrity

```text
MAX_DATA_ITEMS = 500
MAX_RECORD_RESPONSE_BYTES = 5 MiB
MAX_FILE_METADATA_RESPONSE_BYTES = 5 MiB
MAX_INLINE_FILE_BYTES = 1 MiB
MAX_FRAME_SIZE = 10 MiB (unchanged transport cap, still enforced on send)
RESCS_REQUEST_TIMEOUT = 2.0 s, retries = 0
```

Responses are serialized with the existing `MessageSerializer` and
byte-checked before send — never truncated, never silently shrunk. Inline
file bytes are size- and SHA-256-verified; mismatch → 
`DATA_RETRIEVAL_FAILED`.

## Observability

Metrics (`CoreApplication.data_metrics()`, merged from organizer +
transport): `data_requests, data_requests_successful, data_requests_failed,
data_records_retrieved, data_files_retrieved, data_access_denied,
data_source_failures, data_response_too_large, data_messages_sent`.
Events on the existing bus: `DATA_REQUEST_RECEIVED,
DATA_RETRIEVAL_SUCCESS, DATA_RETRIEVAL_FAILURE, DATA_ACCESS_DENIED,
DATA_RESPONSE_SENT`.

## Code map

```text
core/data/
  requests.py      validation (pure)
  rescs_reader.py  RescsDataReader seam + Adapter/Http backends
  normalize.py     normalization, ordering, pagination (pure)
  organizer.py     DataOrganizer coordinator (metrics, events)
  errors.py        typed internal errors
core/communication/tcp.py   DATA_REQUEST branch + distribution delivery
core/application/app.py     lifecycle wiring (factory over current adapter)
```

## Validation status

Implemented and automated-tested via localhost (`127.0.0.1`) simulation:
unit suite (`tests/data/`), HTTP contract suite, and end-to-end
multi-device distribution (`tests/integration/test_data_distribution.py`,
device-a/b/c through real sockets, framing, auth, registration, fixture
HTTP, routing, concurrency, offline and reconnect). Physical-device or
production-R.E.S.C.S. demonstration has NOT been performed.
