#!/usr/bin/env python3
"""Configure, inspect, or capture the M5Stick over its USB serial connection."""

import argparse
import json
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.config import DEFAULT_CONFIG


def connect(port: str | None) -> serial.Serial:
    if port is None:
        candidates = [p.device for p in list_ports.comports() if p.vid in (0x1A86, 0x10C4)]
        if len(candidates) != 1:
            raise SystemExit("Specify --port when there is not exactly one USB serial device")
        port = candidates[0]
    link = serial.Serial(port=None, baudrate=115200, timeout=1, write_timeout=5)
    # Do not reset the board merely by opening a diagnostic connection.
    link.dtr = False
    link.rts = False
    link.port = port
    link.open()
    time.sleep(0.2)
    link.reset_input_buffer()
    return link


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["configure", "status", "screenshot", "reboot", "reconnect"]
    )
    parser.add_argument("--port")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG.with_name("device.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/display.png"))
    args = parser.parse_args()
    message = {"command": args.command}
    if args.command == "configure":
        settings = json.loads(args.config.expanduser().read_text())
        for key in ("ssid", "password", "host", "port", "token"):
            message[key] = settings[key]
    with connect(args.port) as link:
        link.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        deadline = time.monotonic() + 25
        expected = {
            "configure": "configured",
            "status": "status",
            "reboot": "rebooting",
            "reconnect": "wifi_connecting",
        }
        while time.monotonic() < deadline:
            line = link.readline()
            if args.command == "screenshot" and line.startswith(b"FRAME "):
                # Status and configuration do not need the screenshot dependency.
                from PIL import Image  # noqa: PLC0415

                _, width, height, mode = line.decode().split()
                if mode != "RGB888" or (int(width), int(height)) != (240, 135):
                    raise SystemExit("Unexpected frame dimensions")
                pixels = bytearray()
                length = int(width) * int(height) * 3
                deadline = time.monotonic() + 30
                while len(pixels) < length and time.monotonic() < deadline:
                    pixels.extend(link.read(length - len(pixels)))
                if len(pixels) != length:
                    raise SystemExit("Incomplete framebuffer")
                args.output.parent.mkdir(parents=True, exist_ok=True)
                Image.frombytes("RGB", (int(width), int(height)), bytes(pixels)).save(args.output)
                print(f"Saved {args.output}")
                return
            try:
                event = json.loads(line)
            except ValueError, UnicodeDecodeError:
                continue
            if event.get("event") == expected.get(args.command):
                print(json.dumps(event, indent=2))
                return
            if event.get("event") in {"invalid_config", "save_failed", "unknown_command"}:
                raise SystemExit(event["event"])
    raise SystemExit("Device did not acknowledge the command")


if __name__ == "__main__":
    main()
