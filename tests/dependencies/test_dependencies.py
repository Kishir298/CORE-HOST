import pytest

from core.dependencies import DependencyManager
from core.errors import CircularDependencyError, DependencyError


def test_register_component():
    manager = DependencyManager()

    manager.register(
        "memory",
        ["database"],
    )

    assert manager.count() == 1
    assert manager.get_dependencies("memory") == ["database"]


def test_register_without_dependencies():
    manager = DependencyManager()

    manager.register("database")

    assert manager.get_dependencies("database") == []


def test_register_multiple_dependencies():
    manager = DependencyManager()

    manager.register("asis", ["memory", "communication"])

    assert manager.get_dependencies("asis") == [
        "communication",
        "memory",
    ]


def test_add_dependencies_to_existing_component():
    manager = DependencyManager()

    manager.register("asis", ["memory"])
    manager.register("asis", ["communication"])

    assert manager.get_dependencies("asis") == [
        "communication",
        "memory",
    ]


def test_set_dependencies():
    manager = DependencyManager()

    manager.register("memory", ["database"])

    manager.set_dependencies(
        "memory",
        ["rescs"],
    )

    assert manager.get_dependencies("memory") == ["rescs"]


def test_missing_component():
    manager = DependencyManager()

    with pytest.raises(DependencyError):
        manager.get_dependencies("missing")


def test_direct_dependency():
    manager = DependencyManager()

    manager.register("memory", ["database"])
    manager.register("database")

    assert manager.has_dependency(
        "memory",
        "database",
    )

    assert not manager.has_dependency(
        "memory",
        "rescs",
    )


def test_start_order():
    manager = DependencyManager()

    manager.register("database")
    manager.register("memory", ["database"])
    manager.register("asis", ["memory"])

    order = manager.get_start_order("asis")

    assert order == [
        "database",
        "memory",
    ]


def test_multiple_dependencies_start_order():
    manager = DependencyManager()

    manager.register("database")
    manager.register("communication")
    manager.register(
        "memory",
        ["database", "communication"],
    )

    order = manager.get_start_order("memory")

    assert order == [
        "communication",
        "database",
    ]


def test_nested_dependencies():
    manager = DependencyManager()

    manager.register("database")
    manager.register("memory", ["database"])
    manager.register("asis", ["memory"])

    assert manager.get_start_order("asis") == [
        "database",
        "memory",
    ]


def test_missing_dependency():
    manager = DependencyManager()

    manager.register(
        "memory",
        ["database"],
    )

    with pytest.raises(DependencyError):
        manager.get_start_order("memory")


def test_circular_dependency():
    manager = DependencyManager()

    manager.register("a", ["b"])
    manager.register("b", ["a"])

    with pytest.raises(CircularDependencyError):
        manager.get_start_order("a")


def test_long_circular_dependency():
    manager = DependencyManager()

    manager.register("a", ["b"])
    manager.register("b", ["c"])
    manager.register("c", ["a"])

    with pytest.raises(CircularDependencyError):
        manager.validate()


def test_validate_graph():
    manager = DependencyManager()

    manager.register("database")
    manager.register("memory", ["database"])
    manager.register("asis", ["memory"])

    manager.validate()


def test_unregister_component():
    manager = DependencyManager()

    manager.register("database")
    manager.register("memory", ["database"])

    manager.unregister("database")

    assert manager.count() == 1
    assert manager.get_dependencies("memory") == []


def test_clear():
    manager = DependencyManager()

    manager.register("database")
    manager.register("memory", ["database"])

    manager.clear()

    assert manager.count() == 0


def test_duplicate_dependencies_are_removed():
    manager = DependencyManager()

    manager.register(
        "memory",
        ["database", "database"],
    )

    assert manager.get_dependencies("memory") == ["database"]
