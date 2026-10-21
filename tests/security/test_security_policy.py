import pytest

from core.security import (
    Permission,
    SecurityPolicy,
)


def test_default_enforcement_is_inactive():
    policy = SecurityPolicy()

    assert policy.enforced is False


def test_can_enable_and_disable_enforcement():
    policy = SecurityPolicy()

    policy.set_enforced(True)
    assert policy.enforced is True

    policy.set_enforced(False)
    assert policy.enforced is False


def test_set_enforced_rejects_non_boolean():
    policy = SecurityPolicy()

    with pytest.raises(TypeError):
        policy.set_enforced("yes")


def test_grant_and_required():
    policy = SecurityPolicy()
    policy.grant("resources", "register", Permission.WRITE)

    assert (
        policy.required("resources", "register")
        == Permission.WRITE
    )
    assert policy.required("resources", "get") is None


def test_grant_rejects_invalid_permission():
    policy = SecurityPolicy()

    with pytest.raises(TypeError):
        policy.grant("resources", "get", "read")


def test_revoke_removes_requirement():
    policy = SecurityPolicy()
    policy.grant("resources", "register", Permission.WRITE)
    policy.grant("resources", "get", Permission.READ)

    policy.revoke("resources", "register")

    assert policy.required("resources", "register") is None
    assert policy.required("resources", "get") == Permission.READ


def test_revoke_unknown_is_noop():
    policy = SecurityPolicy()
    policy.revoke("resources", "missing")

    assert policy.count() == 0


def test_required_validates_identifiers():
    policy = SecurityPolicy()

    with pytest.raises(ValueError):
        policy.required("", "get")

    with pytest.raises(ValueError):
        policy.required("resources", "")


def test_services_and_count():
    policy = SecurityPolicy()
    policy.grant("resources", "register", Permission.WRITE)
    policy.grant("resources", "get", Permission.READ)
    policy.grant("health", "status", Permission.READ)

    assert policy.services() == ["health", "resources"]
    assert policy.count() == 3


def test_clear_removes_all_requirements():
    policy = SecurityPolicy()
    policy.grant("resources", "get", Permission.READ)
    policy.grant("health", "status", Permission.READ)

    policy.clear()

    assert policy.count() == 0
    assert policy.enforced is False


def test_enforcement_flag_independent_of_requirements():
    policy = SecurityPolicy()
    policy.grant("resources", "get", Permission.READ)
    policy.set_enforced(True)

    assert policy.count() == 1
    assert policy.enforced is True
