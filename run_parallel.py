import argparse
import subprocess
import sys
from pathlib import Path


def build_cmd(python_exe: str, script_dir: Path, device_uri: str, log_dir: Path):
    return [
        python_exe,
        "-m",
        "airtest",
        "run",
        str(script_dir),
        "--device",
        device_uri,
        "--log",
        str(log_dir),
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Run one Airtest project on multiple devices in parallel"
    )
    parser.add_argument(
        "--device",
        action="append",
        required=True,
        help="Device URI, can be provided multiple times",
    )
    parser.add_argument(
        "--script",
        default=str(Path(__file__).resolve().parent),
        help="Path to .air project directory",
    )
    parser.add_argument(
        "--python",
        default=str(
            Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
        ),
        help="Python executable used to run Airtest",
    )
    parser.add_argument(
        "--log-root",
        default=str(Path(__file__).resolve().parent / "parallel_logs"),
        help="Root directory for per-device logs",
    )
    args = parser.parse_args()

    script_dir = Path(args.script).resolve()
    python_exe = str(Path(args.python).resolve())
    log_root = Path(args.log_root).resolve()
    log_root.mkdir(parents=True, exist_ok=True)

    procs = []
    for idx, device in enumerate(args.device, start=1):
        safe_name = (
            device.replace(":", "_")
            .replace("/", "_")
            .replace("?", "_")
            .replace("&", "_")
            .replace("=", "_")
        )
        log_dir = log_root / f"dev{idx}_{safe_name}"
        log_dir.mkdir(parents=True, exist_ok=True)

        cmd = build_cmd(python_exe, script_dir, device, log_dir)
        print(f"[start] device={device}")
        procs.append((device, subprocess.Popen(cmd)))

    exit_codes = []
    for device, proc in procs:
        code = proc.wait()
        exit_codes.append(code)
        print(f"[done] device={device} exit_code={code}")

    if any(code != 0 for code in exit_codes):
        sys.exit(1)


if __name__ == "__main__":
    main()
