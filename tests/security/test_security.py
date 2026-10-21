import pytest

from core.security import (
    AuthorizationError,
    Identity,
    IdentityAlreadyRegistered,
    IdentityNotFound,
    IdentityType,
    Permission,
    SecurityManager,
)


def make_identity(
    identity_id: str = "asis",
    permissions: frozenset[Permission] | None = None,
) -> Identity:
    return Identity(
        identity_id=identity_id,
        name="A.S.I.S.",
        identity_type=IdentityType.SERVICE,
        permissions=permissions or frozenset(),
    )


def test_identity_creation():
    identity = make_identity()

    assert identity.identity_id == "asis"
    assert identity.name == "A.S.I.S."
    assert identity.identity_type == IdentityType.SERVICE
    assert identity.permissions == frozenset()
    assert identity.created_at is not None


def test_register_identity():
    manager = SecurityManager()
    identity = make_identity()

    result = manager.register_identity(identity)

    assert result is identity
    assert manager.get_identity("asis") is identity
    assert manager.count() == 1


def test_duplicate_identity():
    manager = SecurityManager()
    identity = make_identity()

    manager.register_identity(identity)

    with pytest.raises(IdentityAlreadyRegistered):
        manager.register_identity(identity)


def test_missing_identity():
    manager = SecurityManager()

    with pytest.raises(IdentityNotFound):
        manager.get_identity("missing")


def test_authenticate_identity():
    manager = SecurityManager()
    identity = make_identity()

    manager.register_identity(identity)

    authenticated = manager.authenticate("asis")

    assert authenticated is identity


def test_authorized_identity():
    manager = SecurityManager()

    identity = make_identity(
        permissions=frozenset({
            Permission.READ,
            Permission.WRITE,
        })
    )

    manager.register_identity(identity)

    assert manager.authorize(
        "asis",
        Permission.READ,
    )


def test_unauthorized_identity():
    manager = SecurityManager()

    identity = make_identity(
        permissions=frozenset({
            Permission.READ,
        })
    )

    manager.register_identity(identity)

    with pytest.raises(AuthorizationError):
        manager.authorize(
            "asis",
            Permission.ADMIN,
        )


def test_has_permission():
    manager = SecurityManager()

    identity = make_identity(
        permissions=frozenset({
            Permission.READ,
            Permission.EXECUTE,
        })
    )

    manager.register_identity(identity)

    assert manager.has_permission(
        "asis",
        Permission.READ,
    )

    assert manager.has_permission(
        "asis",
        Permission.EXECUTE,
    )

    assert not manager.has_permission(
        "asis",
        Permission.WRITE,
    )


def test_unregister_identity():
    manager = SecurityManager()
    manager.register_identity(make_identity())

    removed = manager.unregister_identity("asis")

    assert removed.identity_id == "asis"
    assert manager.count() == 0

    with pytest.raises(IdentityNotFound):
        manager.get_identity("asis")


def test_list_identities():
    manager = SecurityManager()

    manager.register_identity(make_identity("asis"))
    manager.register_identity(
        Identity(
            identity_id="rovert",
            name="ROVERT",
            identity_type=IdentityType.DEVICE,
        )
    )

    identities = manager.list_identities()

    assert len(identities) == 2
    assert identities[0].identity_id == "asis"
    assert identities[1].identity_id == "rovert"


def test_clear():
    manager = SecurityManager()

    manager.register_identity(make_identity("asis"))
    manager.register_identity(make_identity("tiviss"))

    manager.clear()

    assert manager.count() == 0
