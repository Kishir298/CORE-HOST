import pytest

from core.organization import (
    OrganizationEngine,
    OrganizationEntry,
    OrganizationEntryAlreadyExists,
    OrganizationEntryNotFound,
)


def make_entry(
    entry_id: str = "asis",
    category: str = "ai",
) -> OrganizationEntry:
    return OrganizationEntry(
        entry_id=entry_id,
        category=category,
        name="A.S.I.S.",
        resource_id="asis-main",
        metadata={"version": "0.1"},
    )


def test_entry_creation():
    entry = make_entry()

    assert entry.entry_id == "asis"
    assert entry.category == "ai"
    assert entry.name == "A.S.I.S."
    assert entry.resource_id == "asis-main"
    assert entry.metadata["version"] == "0.1"


def test_add_entry():
    engine = OrganizationEngine()
    entry = make_entry()

    assert engine.add(entry) is entry
    assert engine.get("asis") is entry
    assert engine.count() == 1


def test_duplicate_entry():
    engine = OrganizationEngine()
    entry = make_entry()

    engine.add(entry)

    with pytest.raises(OrganizationEntryAlreadyExists):
        engine.add(entry)


def test_missing_entry():
    engine = OrganizationEngine()

    with pytest.raises(OrganizationEntryNotFound):
        engine.get("missing")


def test_remove_entry():
    engine = OrganizationEngine()
    entry = make_entry()

    engine.add(entry)
    removed = engine.remove("asis")

    assert removed is entry
    assert engine.count() == 0


def test_list_entries():
    engine = OrganizationEngine()

    engine.add(make_entry("asis", "ai"))
    engine.add(make_entry("rovert", "hardware"))

    entries = engine.list()

    assert len(entries) == 2
    assert entries[0].entry_id == "asis"
    assert entries[1].entry_id == "rovert"


def test_filter_by_category():
    engine = OrganizationEngine()

    engine.add(make_entry("asis", "ai"))
    engine.add(make_entry("tiviss", "ai"))
    engine.add(make_entry("rovert", "hardware"))

    ai_entries = engine.by_category("ai")

    assert len(ai_entries) == 2
    assert all(entry.category == "ai" for entry in ai_entries)


def test_filter_by_resource():
    engine = OrganizationEngine()

    engine.add(make_entry("entry-1", "ai"))
    engine.add(
        OrganizationEntry(
            entry_id="entry-2",
            category="service",
            name="Memory",
            resource_id="asis-main",
        )
    )
    engine.add(
        OrganizationEntry(
            entry_id="entry-3",
            category="hardware",
            name="ROVERT",
            resource_id="rovert-main",
        )
    )

    entries = engine.by_resource("asis-main")

    assert len(entries) == 2
    assert {entry.entry_id for entry in entries} == {
        "entry-1",
        "entry-2",
    }


def test_update_entry():
    engine = OrganizationEngine()
    engine.add(make_entry())

    updated = engine.update(
        "asis",
        category="service",
        name="A.S.I.S. Core Service",
        resource_id="asis-v2",
        metadata={"version": "0.2"},
    )

    assert updated.category == "service"
    assert updated.name == "A.S.I.S. Core Service"
    assert updated.resource_id == "asis-v2"
    assert updated.metadata == {"version": "0.2"}


def test_partial_update():
    engine = OrganizationEngine()
    engine.add(make_entry())

    engine.update(
        "asis",
        name="Updated A.S.I.S.",
    )

    entry = engine.get("asis")

    assert entry.name == "Updated A.S.I.S."
    assert entry.category == "ai"
    assert entry.resource_id == "asis-main"


def test_clear():
    engine = OrganizationEngine()

    engine.add(make_entry("one"))
    engine.add(make_entry("two"))

    engine.clear()

    assert engine.count() == 0
    assert engine.list() == []
