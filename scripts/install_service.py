#!/usr/bin/env python3
"""Install this checkout's bridge as a macOS login service."""

import argparse
import errno
import os
import plistlib
import subprocess  # nosec B404 # launchctl runs fixed subcommands without a shell.
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Direct script execution needs the checkout root on sys.path first.
from bridge.config import DEFAULT_CONFIG, load_config  # noqa: E402

LABEL = "local.m5stick-ai-usage"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config_path = args.config.expanduser().resolve()
    try:
        load_config(config_path)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    log = Path.home() / "Library/Logs/m5stick-ai-usage"
    log.mkdir(parents=True, exist_ok=True)
    target = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    target.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            str(ROOT / ".venv/bin/python"),
            "-u",
            str(ROOT / "bridge/server.py"),
            "--config",
            str(config_path),
        ],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ExitTimeOut": 60,
        "ProcessType": "Background",
        "StandardOutPath": str(log / "bridge.log"),
        "StandardErrorPath": str(log / "bridge.log"),
        "EnvironmentVariables": {
            "PATH": (
                f"{Path.home()}/.cargo/bin:/opt/homebrew/bin:/usr/local/bin"
                ":/usr/bin:/bin:/usr/sbin:/sbin"
            )
        },
    }
    target.write_bytes(plistlib.dumps(plist))
    target.chmod(0o644)
    domain = f"gui/{os.getuid()}"
    subprocess.run(  # nosec B603 B607 # fixed launchctl command for this user's service.
        ["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True, check=False
    )
    # bootout can return while launchd is still disposing of the old job.
    # During that interval bootstrap reports EIO even with a valid plist.
    deadline = time.monotonic() + 30
    while True:
        loaded = subprocess.run(  # nosec B603 B607 # fixed launchctl command and local plist.
            ["launchctl", "bootstrap", domain, str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if loaded.returncode == 0:
            break
        if loaded.returncode != errno.EIO or time.monotonic() >= deadline:
            raise SystemExit(loaded.stderr.strip() or "Could not register the bridge")
        time.sleep(0.5)
    print(f"Installed {target}")


if __name__ == "__main__":
    main()
