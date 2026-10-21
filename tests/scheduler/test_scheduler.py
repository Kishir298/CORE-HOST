import pytest

from core.resources.models import create_device_resource
from core.scheduler import (
    AgentProfile,
    AgentScheduler,
    NoSuitableAgent,
    ProfileAlreadyRegistered,
    ProfileNotFound,
)
from core.scheduler.models import Assignment


def make_device(device_id="dev-1", capabilities=None, device_type="phone", platform="ios"):
    return create_device_resource(
        device_id=device_id,
        name=f"Device {device_id}",
        device_type=device_type,
        platform=platform,
        capabilities=capabilities or ["camera", "gps"],
    )


def test_default_profiles_exist():
    s = AgentScheduler()
    assert s.count() == 3
    ids = {p.profile_id for p in s.list_profiles()}
    assert ids == {"asis-local", "asis-offload", "tiviss-compat"}


def test_register_and_unregister_profile():
    s = AgentScheduler()
    p = AgentProfile(profile_id="custom-1", name="Custom", capabilities=["inference"])
    s.register_profile(p)
    assert s.count() == 4
    with pytest.raises(ProfileAlreadyRegistered):
        s.register_profile(p)
    s.unregister_profile("custom-1")
    assert s.count() == 3
    with pytest.raises(ProfileNotFound):
        s.unregister_profile("custom-1")


def test_find_suitable_agent_by_capability():
    s = AgentScheduler()
    device = make_device(capabilities=["camera", "gps"], device_type="phone", platform="ios")
    profile = s.find_suitable_agent(device)
    # asis-offload has camera+gps, phone/ios, so highest score
    assert profile.profile_id == "asis-offload"


def test_find_suitable_agent_explicit_profile():
    s = AgentScheduler()
    device = make_device()
    p = s.find_suitable_agent(device, profile_id="tiviss-compat")
    # tiviss-compat only supports generic, so phone should fallback? Actually supports generic only, so phone != generic, but score still >0 for asis-local vs tiviss? For explicit, check capacity and score >0
    # Since device is phone/ios, tiviss-compat supports generic only, so should return None? Let's check logic: score_for returns 0 if unsupported? Actually tiviss-compat has supported_device_types ["generic"], so phone not in it → supports_device false → but score still? score adds 5 only if type in list else no. So score still 2 (host) + shared? shared 0, so 2. So >0 returns profile.
    assert p is not None
    assert p.profile_id == "tiviss-compat"


def test_find_suitable_agent_capacity():
    s = AgentScheduler()
    # Create small capacity profile
    p = AgentProfile(profile_id="small", name="Small", capabilities=["camera"], max_assignments=1)
    s.register_profile(p)
    device1 = make_device("d1", ["camera"])
    device2 = make_device("d2", ["camera"])
    s.assign(device1, profile_id="small")
    # second assignment to same profile should fail capacity, fallback to other
    profile2 = s.find_suitable_agent(device2, profile_id="small")
    assert profile2 is None


def test_assign_creates_agent_resource_and_assignment():
    s = AgentScheduler()
    device = make_device("dev-assign-1")
    agent, assignment = s.assign(device)
    assert agent.resource_type == "agent"
    assert agent.metadata["target_device"] == "dev-assign-1"
    assert assignment.device_id == "dev-assign-1"
    assert assignment.agent_id == agent.resource_id
    assert s.assignment_count() == 1
    assert s.list_assignments()[0].device_id == "dev-assign-1"


def test_assign_idempotent():
    s = AgentScheduler()
    device = make_device("dev-idem")
    a1, ass1 = s.assign(device)
    a2, ass2 = s.assign(device)
    assert a1.resource_id == a2.resource_id
    assert ass1.agent_id == ass2.agent_id
    assert s.assignment_count() == 1


def test_assign_requires_device_resource():
    s = AgentScheduler()
    with pytest.raises(TypeError):
        s.assign({"resource_id": "fake"})  # type: ignore


def test_no_suitable_agent_when_exhausted():
    s = AgentScheduler()
    # Exhaust all profiles by setting max=0 for fallback handling
    # Instead, create scheduler with empty profiles
    s._profiles.clear()
    s._profile_assignment_counts.clear()
    device = make_device()
    with pytest.raises(NoSuitableAgent):
        s.assign(device)


def test_release_decrements_counts():
    s = AgentScheduler()
    device = make_device("dev-rel")
    agent, ass = s.assign(device, profile_id="asis-local")
    assert s.assignment_count() == 1
    s.release("dev-rel")
    assert s.assignment_count() == 0
    # profile count back to 0, can assign again
    agent2, _ = s.assign(device, profile_id="asis-local")
    assert agent2.resource_id != agent.resource_id  # new agent id


def test_release_unknown_raises():
    s = AgentScheduler()
    with pytest.raises(Exception):
        s.release("unknown-device")


def test_get_assignment_and_agent():
    s = AgentScheduler()
    device = make_device("dev-get")
    agent, ass = s.assign(device)
    fetched_ass = s.get_assignment("dev-get")
    assert fetched_ass.agent_id == agent.resource_id
    fetched_agent = s.get_agent(agent.resource_id)
    assert fetched_agent.resource_id == agent.resource_id


def test_clear_resets():
    s = AgentScheduler()
    device = make_device("dev-clear")
    s.assign(device)
    s.clear()
    assert s.assignment_count() == 0
    assert s.list_agents() == []


def test_profile_scoring_prefers_shared_caps():
    s = AgentScheduler()
    # Device with heart_rate should prefer offload (has heart_rate)
    device = make_device("dev-hr", ["heart_rate"], "watch", "watchos")
    profile = s.find_suitable_agent(device)
    assert profile.profile_id == "asis-offload"
    # Generic device with no caps should return a viable profile (deterministic)
    device2 = make_device("dev-gen", [], "generic", "unknown")
    profile2 = s.find_suitable_agent(device2)
    assert profile2 is not None
    assert profile2.profile_id in ("asis-local", "tiviss-compat", "asis-offload")
    # Ensure scoring is consistent across calls
    profile2_again = s.find_suitable_agent(device2)
    assert profile2.profile_id == profile2_again.profile_id


def test_legacy_payload_compatibility():
    """Legacy assign payload used profile_id optional and device_id mandatory — must still work."""
    s = AgentScheduler()
    device = make_device("legacy-dev", ["voice"])
    # Old code path used assign without profile_id
    agent, ass = s.assign(device)
    assert isinstance(ass, Assignment)
    # Verify agent dict contains legacy keys
    d = agent.to_dict()
    assert "resource_id" in d
    assert "resource_type" in d
    assert d["resource_type"] == "agent"
