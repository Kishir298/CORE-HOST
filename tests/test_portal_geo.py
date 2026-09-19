"""Portal geo/capability mirror tests (offline, fixed fixtures)."""

from __future__ import annotations

from core.portal import (
    distance_between,
    format_distance,
    format_location,
    has_coordinates,
    haversine_meters,
    make_location,
    unknown_location,
)
from core.portal.capabilities import (
    CAPABILITY_HIGH,
    CAPABILITY_LOW,
    CAPABILITY_MEDIUM,
    CAPABILITY_UNKNOWN,
    evaluate_class,
    host_facts,
)

GB = 1024 ** 3
COORD_A = (25.2048, 55.2708)
COORD_B = (25.1972, 55.2744)


def _profile(ram_gb, avail_gb=None, cores=8):
    return {
        "ram_total_bytes": int(ram_gb * GB),
        "ram_available_bytes": int((avail_gb if avail_gb is not None else ram_gb) * GB),
        "cpu_cores": cores,
    }


def test_haversine_reference_pair():
    distance = haversine_meters(*COORD_A, *COORD_B)
    assert 800.0 < distance < 1000.0


def test_distance_requires_both_sides():
    location = make_location(latitude=25.2, longitude=55.2)
    assert distance_between(location, unknown_location()) is None
    assert distance_between(unknown_location(), location) is None


def test_format_bands():
    assert format_distance(None) == "Distance unavailable"
    assert format_distance(350) == "~350 m away"
    assert format_distance(2400) == "~2.4 km away"
    assert format_location(unknown_location()) == "Location: Unknown"
    assert not has_coordinates(unknown_location())


def test_capability_classes_match_client():
    assert evaluate_class(_profile(64, 32, 16)) == CAPABILITY_HIGH
    assert evaluate_class(_profile(16, 4, 8)) == CAPABILITY_MEDIUM
    assert evaluate_class(_profile(4, 1, 2)) == CAPABILITY_LOW
    assert evaluate_class({}) == CAPABILITY_UNKNOWN


def test_host_facts_never_raise():
    facts = host_facts()
    assert isinstance(facts, dict)
    assert "cpu_cores" in facts and "hostname" in facts
