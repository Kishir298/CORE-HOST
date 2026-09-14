import warnings

import pytest

from core.application import CoreApplication
from core.cli.main import create_parser, execute, execute_application
from core.runtime import Runtime, RuntimeState


def test_parser_status():
    parser = create_parser()

    args = parser.parse_args(["status"])

    assert args.command == "status"


def test_parser_start():
    parser = create_parser()

    args = parser.parse_args(["start"])

    assert args.command == "start"


def test_parser_stop():
    parser = create_parser()

    args = parser.parse_args(["stop"])

    assert args.command == "stop"


def test_parser_services():
    parser = create_parser()

    args = parser.parse_args(["services"])

    assert args.command == "services"


def test_parser_resources():
    parser = create_parser()

    args = parser.parse_args(["resources"])

    assert args.command == "resources"


def test_parser_connections():
    parser = create_parser()

    args = parser.parse_args(["connections"])

    assert args.command == "connections"


def test_parser_health():
    parser = create_parser()

    args = parser.parse_args(["health"])

    assert args.command == "health"


def test_parser_agents():
    parser = create_parser()

    args = parser.parse_args(["agents"])

    assert args.command == "agents"


def test_status_command(capsys):
    runtime = Runtime()
    parser = create_parser()

    args = parser.parse_args(["status"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        execute(args, runtime)

    output = capsys.readouterr().out

    assert "Runtime: STOPPED" in output


def test_start_command():
    runtime = Runtime()
    parser = create_parser()

    args = parser.parse_args(["start"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result = execute(args, runtime)

    assert result == 0
    assert runtime.state == RuntimeState.RUNNING


def test_stop_command():
    runtime = Runtime()
    runtime.start()

    parser = create_parser()

    args = parser.parse_args(["stop"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result = execute(args, runtime)

    assert result == 0
    assert runtime.state == RuntimeState.STOPPED


def test_services_command(capsys):
    runtime = Runtime()
    parser = create_parser()

    args = parser.parse_args(["services"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        execute(args, runtime)

    assert "Services:" in capsys.readouterr().out


def test_resources_command(capsys):
    runtime = Runtime()
    parser = create_parser()

    args = parser.parse_args(["resources"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        execute(args, runtime)

    assert "Resources:" in capsys.readouterr().out


def test_connections_command(capsys):
    runtime = Runtime()
    parser = create_parser()

    args = parser.parse_args(["connections"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        execute(args, runtime)

    assert "Connections:" in capsys.readouterr().out


def test_health_command(capsys):
    runtime = Runtime()
    parser = create_parser()

    args = parser.parse_args(["health"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        execute(args, runtime)

    assert "Health:" in capsys.readouterr().out


def test_deprecated_execute_emits_warning():
    runtime = Runtime()
    parser = create_parser()
    args = parser.parse_args(["status"])
    with pytest.warns(DeprecationWarning, match="execute\\(runtime\\) is deprecated"):
        execute(args, runtime)


def test_execute_application_status(capsys):
    app = CoreApplication()
    parser = create_parser()
    args = parser.parse_args(["status"])
    result = execute_application(args, app)
    assert result == 0
    assert "Runtime: STOPPED" in capsys.readouterr().out


def test_execute_application_services(capsys):
    app = CoreApplication()
    parser = create_parser()
    args = parser.parse_args(["services"])
    result = execute_application(args, app)
    assert result == 0
    assert "Services:" in capsys.readouterr().out


def test_execute_application_resources(capsys):
    app = CoreApplication()
    parser = create_parser()
    args = parser.parse_args(["resources"])
    result = execute_application(args, app)
    assert result == 0
    assert "Resources:" in capsys.readouterr().out


def test_execute_application_connections(capsys):
    app = CoreApplication()
    parser = create_parser()
    args = parser.parse_args(["connections"])
    result = execute_application(args, app)
    assert result == 0
    assert "Connections:" in capsys.readouterr().out


def test_execute_application_health(capsys):
    app = CoreApplication()
    parser = create_parser()
    args = parser.parse_args(["health"])
    result = execute_application(args, app)
    assert result == 0
    assert "Health:" in capsys.readouterr().out


def test_execute_application_agents(capsys):
    app = CoreApplication()
    parser = create_parser()
    args = parser.parse_args(["agents"])
    result = execute_application(args, app)
    assert result == 0
    assert "Agents:" in capsys.readouterr().out


def test_parser_no_subcommand_is_none():
    parser = create_parser()
    args = parser.parse_args([])
    assert args.command is None


def test_main_no_subcommand_starts_host(monkeypatch):
    import core.cli.main as cli_main

    called = {}

    def fake_run(app):
        called["ran"] = True
        return 0

    monkeypatch.setattr(cli_main, "run_application", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["core", "--config", "config/core.yaml"]
    )
    assert cli_main.main() == 0
    assert called.get("ran") is True


def test_main_start_still_starts_host(monkeypatch):
    import core.cli.main as cli_main

    called = {}

    def fake_run(app):
        called["ran"] = True
        return 0

    monkeypatch.setattr(cli_main, "run_application", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["core", "--config", "config/core.yaml", "start"]
    )
    assert cli_main.main() == 0
    assert called.get("ran") is True


def test_execute_application_unknown_is_actionable_error(capsys):
    import argparse

    app = CoreApplication()
    args = argparse.Namespace(command="bogus-cmd")
    result = execute_application(args, app)
    assert result == 2
    assert "unknown command" in capsys.readouterr().out.lower()
