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


def resolve_device_uri(cli_value: str | None):
    if cli_value and cli_value.strip():
        return cli_value.strip()
    value = input(
        "Input device URI (e.g. android://127.0.0.1:5037/R83Y80GC52R): "
    ).strip()
    if not value:
        raise ValueError("Device URI is required")
    return value


def main():
    parser = argparse.ArgumentParser(
        description="Run one Airtest project on a single device"
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Device URI (if omitted, script will ask you to input it)",
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
        default=str(Path(__file__).resolve().parent / "single_logs"),
        help="Root directory for logs",
    )
    args = parser.parse_args()

    try:
        device = resolve_device_uri(args.device)
    except ValueError as exc:
        print(exc)
        sys.exit(2)

    script_dir = Path(args.script).resolve()
    python_exe = str(Path(args.python).resolve())
    log_root = Path(args.log_root).resolve()

    safe_name = (
        device.replace(":", "_")
        .replace("/", "_")
        .replace("?", "_")
        .replace("&", "_")
        .replace("=", "_")
    )
    log_dir = log_root / safe_name
    log_dir.mkdir(parents=True, exist_ok=True)

    cmd = build_cmd(python_exe, script_dir, device, log_dir)
    print(f"[start] device={device}")
    print("[cmd]", " ".join(cmd))
    result = subprocess.run(cmd)
    print(f"[done] exit_code={result.returncode}")
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
