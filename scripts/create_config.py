#!/usr/bin/env python3
"""Create or update local settings without embedding account names in the code."""

import argparse
import getpass
import json
import secrets
import subprocess  # nosec B404 # scutil is invoked with fixed arguments and no shell.
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.config import (
    DEFAULT_CONFIG,
    DEFAULT_SOURCE,
    generated_paths,
    validate_config,
    validate_config_path,
    validate_source_path,
)
from bridge.server import atomic_json
from bridge.sources import available_accounts, selected_usage_config, write_usage_config


# Keep setup validation together before writing configuration files.
def main():  # noqa: PLR0912, PLR0915
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ssid", help="Omit to retain existing Wi-Fi credentials")
    parser.add_argument("--host", help="Mac's Bonjour hostname; detected on first setup")
    parser.add_argument("--source-config", type=Path, help="Original ai-usagebar config")
    parser.add_argument(
        "--account",
        action="append",
        metavar="ID=LABEL",
        help="Repeat for each row, in display order; replaces the current selection",
    )
    parser.add_argument(
        "--list-accounts", action="store_true", help="List selectable IDs without fetching quotas"
    )
    args = parser.parse_args()
    try:
        bridge_file = validate_config_path(args.config)
        directory = bridge_file.parent
        device_file = directory / "device.json"
        outputs = (bridge_file, *generated_paths(directory))
        source_path = (
            validate_source_path(args.source_config, *outputs)
            if args.source_config is not None
            else None
        )
        bridge = json.loads(bridge_file.read_text()) if bridge_file.exists() else {}
        if source_path is None:
            source_path = validate_source_path(
                Path(bridge.get("source_config", DEFAULT_SOURCE)), *outputs
            )
        source = tomllib.loads(source_path.read_text())
        if args.list_accounts:
            print("\n".join(available_accounts(source)))
            return
        if args.account is not None:
            rows = []
            for item in args.account:
                source_id, separator, label = item.rpartition("=")
                if not separator:
                    raise ValueError("--account must be ID=LABEL")
                rows.append({"id": source_id, "label": label})
            bridge["accounts"] = rows
        if "accounts" not in bridge:
            raise ValueError(
                "Select 1 to 3 accounts with --account ID=LABEL; use --list-accounts to find IDs"
            )
        bridge.setdefault("token", secrets.token_urlsafe(32))
        bridge["source_config"] = str(source_path)
        bridge = validate_config(bridge)
        selected_usage_config(source, bridge["accounts"])
        device = json.loads(device_file.read_text()) if device_file.exists() else {}
        if args.ssid is not None:
            device["ssid"] = args.ssid
        if not isinstance(device.get("ssid"), str) or not 1 <= len(device["ssid"].encode()) <= 32:
            raise ValueError("Supply --ssid with 1 to 32 bytes for initial setup")
        if args.ssid is not None or "password" not in device:
            device["password"] = getpass.getpass("Wi-Fi password: ")
        if not isinstance(device["password"], str) or len(device["password"].encode()) > 63:
            raise ValueError("Wi-Fi password must contain at most 63 bytes")
        host = args.host or device.get("host")
        if not host:
            hostname = subprocess.check_output(  # nosec B603 B607 # fixed macOS scutil command.
                ["scutil", "--get", "LocalHostName"], text=True
            )
            host = hostname.strip() + ".local"
        device["host"] = host
        if not isinstance(device["host"], str) or not 1 <= len(device["host"].encode()) <= 253:
            raise ValueError("Host must contain 1 to 253 bytes")
        device.update(port=bridge["port"], token=bridge["token"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_usage_config(source_path, directory / "ai-usagebar.toml", bridge["accounts"])
    atomic_json(bridge_file, bridge)
    atomic_json(device_file, device)
    print(f"Saved {len(bridge['accounts'])} account rows in {bridge_file}")
    print(f"Device network settings: {device_file}")


if __name__ == "__main__":
    main()
