"""Installed Windows-first host command with a stable per-user data directory."""
import argparse
import os
from pathlib import Path


def default_data():
    if os.environ.get("CORE_WEB_DATA"):
        return Path(os.environ["CORE_WEB_DATA"]).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "RISARMS" / "CORE" / "web"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="core-host", description="Run the C.O.R.E. host and authenticated portal.")
    parser.add_argument("command", nargs="?", choices=("start", "doctor"), default="start")
    parser.add_argument("--data", type=Path, default=None, help="Persistent portal data directory.")
    parser.add_argument("--config", type=Path, help="Optional native CORE YAML configuration.")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    data = (args.data or default_data()).expanduser().resolve()
    if args.command == "doctor":
        print("C.O.R.E. host command is installed.")
        print("Portal data:", data)
        print("Portal settings:", "present" if (data / "gateway.json").is_file() else "first-run setup required")
        print("Local URL:", f"http://127.0.0.1:{args.port}")
        print("External service dependencies and models require separate verification.")
        return 0
    from core.application import CoreApplication
    from core.web.gateway import serve
    # Do not implicitly load config from whichever directory the terminal uses.
    config = args.config.expanduser().resolve() if args.config else None
    runtime = data.parent / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    previous = Path.cwd()
    try:
        os.chdir(runtime)
        app = CoreApplication(config_path=config, discover_config=False)
        serve(app, directory=data, port=args.port, open_browser=not args.no_browser)
    finally:
        os.chdir(previous)
    return 0
