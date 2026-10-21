"""OrganizationEngine concurrency tests (real threads, deterministic)."""

from concurrent.futures import ThreadPoolExecutor

from core.organization import OrganizationEngine, OrganizationEntry
from core.resources import Resource


def make_entry(i):
    return OrganizationEntry(
        entry_id=f"e-{i}",
        category="device",
        name=f"Device {i}",
        resource_id=f"r-{i}",
        metadata={"i": i},
    )


def test_concurrent_add_unique_entries():
    engine = OrganizationEngine()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: engine.add(make_entry(i)), range(100)))
    assert engine.count() == 100
    assert engine.get("e-0").name == "Device 0"
    assert engine.get("e-99").metadata == {"i": 99}


def test_concurrent_categorize_same_resource():
    engine = OrganizationEngine()
    resource = Resource(
        resource_id="shared",
        name="Shared",
        resource_type="device",
        owner="o",
        source="s",
        capabilities=["c"],
        metadata={"k": "v"},
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: engine.categorize_resource(resource), range(50))
        )
    assert engine.count() == 1
    entry = engine.get("resource:shared")
    assert entry.metadata["owner"] == "o"
    assert all(r.entry_id == "resource:shared" for r in results)


def test_concurrent_update_same_entry():
    engine = OrganizationEngine()
    engine.add(make_entry(0))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda i: engine.update("e-0", metadata={"i": i}), range(50)
            )
        )
    entry = engine.get("e-0")
    assert entry.metadata["i"] in set(range(50))
    assert engine.count() == 1


def test_concurrent_reads_and_writes():
    engine = OrganizationEngine()
    errors = []

    def writer(i):
        try:
            engine.add(make_entry(f"w-{i}"))
        except Exception as exc:  # pragma: no cover - must not happen
            errors.append(exc)

    def reader(_):
        try:
            engine.list()
            engine.count()
            engine.by_category("device")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, range(50)))
        list(pool.map(reader, range(50)))
    assert errors == []
    assert engine.count() == 50


def test_concurrent_remove_and_lookup():
    engine = OrganizationEngine()
    for i in range(20):
        engine.add(make_entry(i))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: engine.remove(f"e-{i}"), range(20)))
    assert engine.count() == 0
    assert engine.list() == []


def test_concurrent_clear_and_list():
    engine = OrganizationEngine()
    for i in range(30):
        engine.add(make_entry(i))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(engine.list) for _ in range(20)]
        pool.submit(engine.clear).result()
        results = [f.result() for f in futures]
    assert engine.count() == 0
    assert all(isinstance(r, list) for r in results)


def test_iteration_snapshot_survives_mutation():
    engine = OrganizationEngine()
    for i in range(10):
        engine.add(make_entry(i))
    snapshot = list(iter(engine))
    for i in range(10, 20):
        engine.add(make_entry(i))
    assert len(snapshot) == 10
    assert engine.count() == 20
    # Iterating the snapshot never raises even though the live dict grew.
    assert [e.entry_id for e in snapshot][0] == "e-0"
