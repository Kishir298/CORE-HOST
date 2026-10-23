"""Windows signal branches simulated; macOS/Linux real process tests live in ASIS."""
import signal
import subprocess
from unittest.mock import Mock

from core.web import services


def test_windows_stop_sends_break_before_force(monkeypatch, tmp_path):
    manager = services.Services(tmp_path, {}, "unused")
    job = services.Job("job", "profile", "asis", status="running")
    process = Mock()
    process.poll.return_value = None
    job.process = process
    manager.jobs[job.id] = job
    monkeypatch.setattr(services.os, "name", "nt")
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    force = Mock()
    monkeypatch.setattr(subprocess, "run", force)
    manager.stop(job.id, {"id": "profile", "role": "member"})
    process.send_signal.assert_called_once_with(1)
    force.assert_not_called()
    assert job.status == "cancelled"


def test_windows_stop_force_is_bounded_fallback(monkeypatch, tmp_path):
    manager = services.Services(tmp_path, {}, "unused")
    job = services.Job("job", "profile", "asis", status="running")
    process = Mock(pid=12345)
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("asis", 3)
    job.process = process
    manager.jobs[job.id] = job
    monkeypatch.setattr(services.os, "name", "nt")
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    force = Mock()
    monkeypatch.setattr(subprocess, "run", force)
    manager.stop(job.id, {"id": "profile", "role": "member"})
    force.assert_called_once_with(["taskkill", "/PID", "12345", "/T", "/F"], capture_output=True, timeout=10)
