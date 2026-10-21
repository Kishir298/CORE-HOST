"""One-command host bootstrap. Run from any current working directory."""
import json
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info < (3, 10):
        raise SystemExit("C.O.R.E. needs Python 3.10 or newer.")
    python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if "doctor" in sys.argv[1:]:
        print("C.O.R.E. web doctor")
        print("Host source:", ROOT)
        print("Python environment:", "ready" if python.is_file() else "not created; run this launcher without doctor")
        for name in ("ASIS", "ASCS", "RESCS", "TIVISS", "ROVERT"):
            print(name+":", "source found" if (ROOT.parent/name).is_dir() else "set its source path in portal Settings")
        path = ROOT / "var/web/gateway.json"
        if path.exists():
            config = json.loads(path.read_text())
            print("Public origin:", config.get("public_origin") or "not configured; local browser only")
            print("RESCS key:", "configured" if config.get("rescs_key") else "missing")
        return 0
    if not python.is_file():
        print("Creating C.O.R.E. Python environment…")
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
        subprocess.check_call([str(python), "-m", "pip", "install", "-e", str(ROOT)])
    else:
        probe = subprocess.run([str(python), "-c", "import yaml"], capture_output=True)
        if probe.returncode != 0:
            subprocess.check_call([str(python), "-m", "pip", "install", "-e", str(ROOT)])
    return subprocess.call([str(python), "-m", "core", "web", "--open", *sys.argv[1:]], cwd=ROOT)


if __name__ == "__main__": raise SystemExit(main())
