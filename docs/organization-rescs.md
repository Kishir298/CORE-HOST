# R.E.S.C.S. → C.O.R.E. Organization Ingestion

How stored R.E.S.C.S. data enters the C.O.R.E. Organization index:
retrieved through the adapter, validated against a fixed contract,
normalized into the existing `Resource` model, then organized into
stable `OrganizationEntry` records for Routing, Services and Agents.

```text
                 R.E.S.C.S.
                    │
                    │ stored resource
                    ▼
             R.E.S.C.S. Adapter
          (fetch_resource / list_resources)
                    │
                    │ raw response (external input)
                    ▼
          validate_rescs_resource
                    │
                    │ contract dict
                    ▼
            normalize_resource
                    │
                    │ C.O.R.E. Resource
                    ▼
             ResourceRegistry
          (register new / update existing)
                    │
                    ▼
             OrganizationEngine
          (categorize_resource -> resource:<id>)
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       Routing    Services   Agents
```

R.E.S.C.S. owns persistent storage. The C.O.R.E. resource layer
represents/registers resources inside C.O.R.E. Organization categorizes,
indexes, links and discovers. Organization never stores and never touches
database or HTTP internals — it depends only on the C.O.R.E.-side
adapter interface.

## Resource contract

Every R.E.S.C.S. response consumed here must provide:

```text
resource_id      non-empty string (stable identity)
resource_type    non-empty string (becomes the entry category)
name             non-empty string
owner            non-empty string, or absent (normalizes to None)
metadata         object, or absent (normalizes to {})
```

Optional pass-throughs: `source` (non-empty string), `capabilities`
(list of strings). Anything else (storage paths, internal database ids,
backend timestamps) is intentionally dropped — R.E.S.C.S. remains its
owner and it can always be re-fetched by `resource_id`.

Violations raise `InvalidResourceData`. Nothing is fabricated: missing
required fields fail instead of producing default resources.

## Normalization

`normalize_resource()` maps the validated contract onto the existing
`Resource` model (no duplicate model). Deterministic: the same logical
resource always yields the same `resource_id`, hence the same entry id
`resource:<id>` and category. `registered_at`/`last_seen` are C.O.R.E.-side
lifecycle fields, not storage clocks.

## Ingestor (`ResourceIngestor`)

Constructed with `(adapter, registry, organization)` — e.g. via
`CoreApplication.create_organization_ingestor()`, which always binds the
currently active adapter:

- `ingest_resource(id)` — fetch → validate → normalize → register new or
  update existing in place (entry id unchanged). Idempotent: ten calls
  still yield exactly one entry. A changed `resource_type` re-registers
  so the category stays exact.
- `ingest_all()` — every resource in `resource_id` order; returns
  `{ingested, updated, failed, errors}`. Invalid items are collected,
  never aborting the run.
- `forget_resource(id)` — drops the C.O.R.E.-side registry/organization
  entry only. Never deletes from R.E.S.C.S. storage.

## Deletions vs failures (different states)

- Backend unreachable/timeout → `RescsUnavailable`; existing index data
  is left untouched.
- Fetch explicitly returns nothing → the C.O.R.E.-side entry is removed
  (if present) and `RescsResourceNotFound` is raised.
- Explicit operator removal → `forget_resource()`.

## Errors

`IngestionError` (base) → `InvalidResourceData`, `RescsUnavailable`,
`RescsResourceNotFound`, all under the existing `OrganizationError`.
Backend exceptions are translated, never leaked; nothing is swallowed
into `None`. Organization boundary violations raise
`OrganizationValidationError` (also under `OrganizationError`).

## Organization responsibilities

Categorization (`categorize_resource` / `organize_resource`), indexing
(stable `resource:<id>` entries), discovery (`discover()` over
`by_category` / `by_resource` / `resource()`), resource linkage (attached
`ResourceRegistry`), and authoritative reconciliation (`reconcile()`).

## Dedicated organization API

Higher-level callers use `OrganizationEngine` alone — no direct
`ResourceRegistry`, adapter, or `ResourceIngestor` handling:

```python
organization.ingest_resource("r1")   # -> Resource (fetch→validate→upsert→categorize)
organization.ingest_all()            # -> {ingested, updated, failed, errors}
organization.reconcile()             # -> {added, updated, removed, unchanged, failed, errors}
organization.discover(category="sensor")
organization.discover(resource_id="r1")
organization.discover(category="sensor", resource_id="r1")
organization.resource("r1")          # -> underlying Resource
organization.forget_resource("r1")   # C.O.R.E.-side only, never deletes R.E.S.C.S.
```

`discover()` is pure (no R.E.S.C.S. I/O, no mutation) and returns a
snapshot list, so it works even without an attached ingestor. Write paths
(`ingest_*` / `reconcile` / `forget_resource`) delegate to the attached
`ResourceIngestor` and raise `OrganizationError` when none is attached.

R.E.S.C.S. is the persistence authority. C.O.R.E. Organization is an
in-memory organizational/indexing layer. Organization never persists.

## Organization metadata contract

`categorize_resource()` preserves exactly:

```text
resource_type, owner, source, status,
capabilities (copied list),
metadata (deep-copied dict),
connection_info (deep-copied dict)
```

Top-level `category` (= `resource_type`), `name`, and `resource_id` mirror
the resource. `last_seen` / `registered_at` are intentionally excluded:
they are C.O.R.E.-side lifecycle fields, not organizational identity.
Mutable values are defensively copied in both directions, so mutating a
`Resource` after categorization never changes the stored entry without an
explicit re-categorization, and mutating an entry never changes the
`Resource`. Re-categorization replaces metadata wholesale; a
`resource_type` change updates the category in place without duplicating
the stable entry.

## Reconciliation

`ResourceIngestor.reconcile()` (also reachable as
`OrganizationEngine.reconcile()` when an ingestor is attached):

1. `list_resources()` from R.E.S.C.S. (failure → `RescsUnavailable`, zero
   mutations).
2. Validate + normalize each item (invalid → `failed`/`errors`, valid items
   still processed, invalid items never cause deletion).
3. Add missing, update changed (type change re-registers, same stable id),
   heal unchanged entries, remove explicitly absent ids from the registry
   (which cascades to organization entries).
4. Return deterministic `{added, updated, removed, unchanged, failed,
   errors}` with sorted id lists.

Successful authoritative absence → removal. Backend failure → preservation.
Empty successful list → reconcile to zero (distinct from failure).
Repeated runs without changes are idempotent.

## Thread safety

`OrganizationEngine` holds one `threading.RLock` (same convention as the
R.E.S.C.S. adapters). All `_entries` access is guarded; `list`,
`by_category`, `by_resource`, and `__iter__` return snapshots. `resource()`
copies the registry reference under lock and calls out without holding it,
so no lock-ordering deadlock with `ResourceRegistry` / R.E.S.C.S.

## Validation status

Implemented and automated-tested via localhost suites
(`tests/organization/test_organization_ingestion.py`: contract,
normalization, idempotency, updates, deletion, failure safety, discovery).
No network or physical-device integration is involved in this boundary.
