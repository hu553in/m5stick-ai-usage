"""Local settings and the display's capacity limits."""

import copy
import json
import re
import shutil
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config/m5stick-ai-usage/bridge.json"
DEFAULT_SOURCE = Path.home() / "Library/Application Support/ai-usagebar/config.toml"
MAX_ACCOUNTS = 3
ALL_INTERFACES = "0.0.0.0"  # nosec B104 # The authenticated display connects over the LAN.
DEFAULT_DISPLAY = {
    "poll_seconds": 15,
    "offline_seconds": 45,
    "brightness": 160,
    "warning_percent": 80,
    "critical_percent": 95,
}


def accounts_config(value: object) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ACCOUNTS:
        raise ValueError(f"accounts must contain 1 to {MAX_ACCOUNTS} rows")
    ids, labels = set(), set()
    result = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("each account needs id and label")
        source_id, label = row.get("id"), row.get("label")
        if (
            not isinstance(source_id, str)
            or not 1 <= len(source_id) <= 128
            or not source_id.isascii()
            or not source_id.isprintable()
            or source_id.strip() != source_id
        ):
            raise ValueError("account id must contain 1 to 128 printable ASCII characters")
        if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9]{1,2}", label):
            raise ValueError("account label must contain 1 or 2 ASCII letters or digits")
        if source_id in ids or label in labels:
            raise ValueError("account ids and labels must be unique")
        ids.add(source_id)
        labels.add(label)
        result.append({"id": source_id, "label": label})
    return result


def integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def display_config(value: object) -> dict:
    if not isinstance(value, dict) or value.keys() - DEFAULT_DISPLAY.keys():
        raise ValueError("unknown display setting")
    result = DEFAULT_DISPLAY | value
    integer(result["poll_seconds"], "display.poll_seconds", 5, 300)
    integer(result["offline_seconds"], "display.offline_seconds", result["poll_seconds"], 3600)
    integer(result["brightness"], "display.brightness", 1, 255)
    integer(result["warning_percent"], "display.warning_percent", 0, 100)
    integer(result["critical_percent"], "display.critical_percent", result["warning_percent"], 100)
    return result


def validate_config(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("bridge config must be an object")
    known = {
        "accounts",
        "token",
        "listen",
        "port",
        "poll_seconds",
        "max_age",
        "ai_usagebar",
        "source_config",
        "display",
    }
    if value.keys() - known:
        raise ValueError("unknown bridge settings: " + ", ".join(sorted(value.keys() - known)))
    config = {
        "listen": ALL_INTERFACES,
        "port": 8765,
        "poll_seconds": 120,
        "max_age": 300,
        "ai_usagebar": shutil.which("ai-usagebar") or str(Path.home() / ".cargo/bin/ai-usagebar"),
        "source_config": str(DEFAULT_SOURCE),
    } | copy.deepcopy(value)
    token = config.get("token")
    if (
        not isinstance(token, str)
        or not 32 <= len(token) <= 128
        or not token.isascii()
        or not token.isprintable()
    ):
        raise ValueError("token must contain 32 to 128 printable ASCII characters")
    config["accounts"] = accounts_config(config.get("accounts"))
    config["display"] = display_config(config.get("display", {}))
    integer(config["port"], "port", 1, 65535)
    integer(config["poll_seconds"], "poll_seconds", 60, 3600)
    integer(config["max_age"], "max_age", 30, 3600)
    for key in ("listen", "ai_usagebar", "source_config"):
        setting = config[key]
        if not isinstance(setting, str) or not setting.strip():
            raise ValueError(f"{key} must be a nonempty string")
    return config


def generated_paths(directory: Path) -> tuple[Path, ...]:
    return (directory / "device.json", directory / "ai-usagebar.toml", directory / "snapshot.json")


def _same_path(first: Path, second: Path) -> bool:
    # Compare resolved paths without case, including dangling links on macOS.
    return str(first.resolve()).casefold() == str(second.resolve()).casefold() or (
        first.exists() and second.exists() and first.samefile(second)
    )


def validate_config_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    for generated in generated_paths(path.parent):
        for reserved in (generated, generated.with_suffix(".tmp")):
            if _same_path(path, reserved):
                raise ValueError(
                    f"--config conflicts with generated file {reserved.name}; use another filename"
                )
    return path


def validate_source_path(source_path: Path, *outputs: Path) -> Path:
    source_path = source_path.expanduser().resolve()
    for output in outputs:
        for reserved in (output, output.with_suffix(".tmp")):
            if _same_path(source_path, reserved):
                raise ValueError(
                    f"source_config conflicts with output file {reserved.name}; "
                    "use a separate original config"
                )
    return source_path


def load_config(path: Path) -> dict:
    path = validate_config_path(path)
    config = validate_config(json.loads(path.read_text()))
    validate_source_path(Path(config["source_config"]), path, *generated_paths(path.parent))
    return config
