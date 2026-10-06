#!/usr/bin/env python3
"""Check a provisioned device, including a brief outage of this project's bridge."""

import argparse
import json
import os
import re
import subprocess  # nosec B404 # hardware checks invoke local tools without a shell.
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Direct script execution needs the checkout root on sys.path first.
from bridge.config import ALL_INTERFACES, DEFAULT_CONFIG, MAX_ACCOUNTS, load_config  # noqa: E402
from scripts.install_service import LABEL  # noqa: E402

ARTIFACTS = ROOT / "artifacts"
TARGET = f"gui/{os.getuid()}/{LABEL}"
DEVICE_PORT = None


def device(command, *args):
    port_args = ["--port", DEVICE_PORT] if DEVICE_PORT else []
    result = subprocess.check_output(  # nosec B603 # run this checkout's device script; no shell.
        [sys.executable, str(ROOT / "scripts/device.py"), command, *port_args, *args],
        text=True,
        cwd=ROOT,
        timeout=40,
    )
    return json.loads(result) if command != "screenshot" else result.strip()


def wait_for(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = device("status")
        if predicate(last):
            return last
        time.sleep(2)
    raise AssertionError(f"Device condition not reached: {last}")


def online(status):
    return (
        status["configured"]
        and status["wifi"]
        and not status["offline"]
        and status["now"] > 0
        and 1 <= len(status["accounts"]) <= MAX_ACCOUNTS
    )


def values(status):
    return [
        (r["id"], r["label"], r.get("short_used"), r.get("week_used")) for r in status["accounts"]
    ]


def service_pid():
    state = subprocess.check_output(  # nosec B603 B607 # fixed launchctl query for this service.
        ["launchctl", "print", TARGET], text=True, timeout=10
    )
    match = re.search(r"^\s*pid = (\d+)\s*$", state, re.MULTILINE)
    return int(match.group(1)) if match else None


# The ordered hardware scenario includes outage recovery and cleanup in one flow.
def main():  # noqa: PLR0915
    global DEVICE_PORT  # noqa: PLW0603 - shared serial selection for this single-run test.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--port", help="USB serial port; normally autodetected")
    args = parser.parse_args()
    DEVICE_PORT = args.port
    config_path = args.config.expanduser().resolve()
    config = load_config(config_path)
    device_settings = json.loads(config_path.with_name("device.json").read_text())
    ARTIFACTS.mkdir(exist_ok=True)
    report = []

    def record(name, status):
        report.append({"check": name, "passed": True, "status": status})
        print(f"PASS {name}", flush=True)

    initial = wait_for(online)
    assert (initial["width"], initial["height"]) == (240, 135)
    assert initial["host"] == device_settings["host"]
    assert [r["id"] for r in initial["accounts"]] == [r["id"] for r in config["accounts"]]
    record("Wi-Fi and configured account rows", initial)

    # Convert the listener's wildcard address to a loopback client destination.
    host = "127.0.0.1" if config["listen"] == ALL_INTERFACES else config["listen"]
    request = urllib.request.Request(
        f"http://{host}:{config['port']}/v1/status",
        headers={"Authorization": "Bearer " + config["token"]},
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310 # fixed http scheme.
        payload = json.load(response)
    expected = [
        (
            r["id"],
            r["label"],
            r["short"]["used"] if r["short"] else None,
            r["week"]["used"] if r["week"] else None,
        )
        for r in payload["accounts"]
    ]
    matched = wait_for(lambda status: online(status) and values(status) == expected)
    record("Device percentages match the live bridge", matched)
    device("screenshot", "--output", str(ARTIFACTS / "display-live.png"))

    uptime_before = device("status")["uptime_ms"]
    device("reboot")
    restarted = wait_for(lambda status: online(status) and status["uptime_ms"] < uptime_before)
    record("Reboot retains provisioned settings and reconnects", restarted)

    device("reconnect")
    record("Wi-Fi reassociation and hostname rediscovery", wait_for(online))

    before_outage = device("status")
    try:
        subprocess.run(  # nosec B603 B607 # intentionally stop this test's local bridge service.
            ["launchctl", "bootout", TARGET], check=True, timeout=55
        )
        stopped = wait_for(lambda status: status["offline"] and status["wifi"])
        assert values(stopped) == values(before_outage), "Offline mode lost displayed quotas"
        assert stopped["now"] > before_outage["now"], "Local countdown clock stopped"
        record("Bridge outage retains quotas and local clock", stopped)
        device("screenshot", "--output", str(ARTIFACTS / "display-offline.png"))
    finally:
        subprocess.run(  # nosec B603 # restore this checkout's service with the same local config.
            [
                sys.executable,
                str(ROOT / "scripts/install_service.py"),
                "--config",
                str(config_path),
            ],
            check=True,
            timeout=60,
        )
    record("Bridge restart recovers automatically", wait_for(online))

    previous_pid = service_pid()
    assert previous_pid is not None, "Bridge process is not running"
    subprocess.run(  # nosec B603 B607 # exercise crash recovery for this test's bridge service.
        ["launchctl", "kill", "SIGKILL", TARGET], check=True, timeout=10
    )
    # Let launchd observe the exit before checking its replacement.
    time.sleep(12)
    replacement_pid = service_pid()
    assert replacement_pid is not None and replacement_pid != previous_pid, (
        "launchd did not replace the process"
    )
    record("launchd restarts the bridge after process failure", wait_for(online))
    device("screenshot", "--output", str(ARTIFACTS / "display-final.png"))
    (ARTIFACTS / "hardware-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {len(report)} hardware checks and display captures", flush=True)


if __name__ == "__main__":
    main()
