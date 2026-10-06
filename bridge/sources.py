"""Select configured Codex/Claude sources without copying credentials."""

import json
import os
import tomllib
from pathlib import Path

from bridge.config import accounts_config, validate_source_path


def named_accounts(source: dict) -> dict:
    anthropic = source.get("anthropic", {})
    named = {}
    if anthropic.get("accounts_dir"):
        directory = Path(anthropic["accounts_dir"]).expanduser()
        if directory.is_dir():
            named = {
                p.name: {"label": p.name, "credentials_path": str(p / ".credentials.json")}
                for p in sorted(directory.iterdir())
                if p.is_dir()
            }
    named.update({row["label"]: row for row in anthropic.get("accounts", [])})
    return named


def available_accounts(source: dict) -> list[str]:
    return ["openai", "anthropic", *("anthropic@" + name for name in named_accounts(source))]


def selected_usage_config(source: dict, accounts: list[dict]) -> str:
    accounts = accounts_config(accounts)
    ids = [row["id"] for row in accounts]
    named = named_accounts(source)
    available = {"openai", "anthropic", *("anthropic@" + name for name in named)}
    missing = set(ids) - available
    if missing:
        raise ValueError("Unknown or unsupported account IDs: " + ", ".join(sorted(missing)))
    anthropic = source.get("anthropic", {})
    claude = any(i == "anthropic" or i.startswith("anthropic@") for i in ids)
    lines = [
        "[anthropic]",
        f"enabled = {str(claude).lower()}",
        f"show_default_account = {str('anthropic' in ids).lower()}",
    ]
    if claude:
        lines.extend(
            key + " = " + json.dumps(anthropic[key], ensure_ascii=False)
            for key in ("credentials_path", "desktop_profiles_dir")
            if anthropic.get(key)
        )
    for source_id in ids:
        if not source_id.startswith("anthropic@"):
            continue
        label = source_id.removeprefix("anthropic@")
        row = named[label]
        if not row.get("credentials_path"):
            raise ValueError(f"ai-usagebar account {label} needs credentials_path")
        lines += [
            "",
            "[[anthropic.accounts]]",
            "label = " + json.dumps(label, ensure_ascii=False),
            "credentials_path = " + json.dumps(row["credentials_path"], ensure_ascii=False),
        ]
    lines += ["", "[openai]", f"enabled = {str('openai' in ids).lower()}"]
    if "openai" in ids and source.get("openai", {}).get("codex_auth_path"):
        lines.append(
            "codex_auth_path = "
            + json.dumps(source["openai"]["codex_auth_path"], ensure_ascii=False)
        )
    # Z.AI and OpenRouter are enabled by default in ai-usagebar 1.32.
    for vendor in ("copilot", "zai", "openrouter"):
        lines += ["", f"[{vendor}]", "enabled = false"]
    return "\n".join(lines) + "\n"


def write_usage_config(source_path: Path, target: Path, accounts: list[dict]) -> Path:
    source_path = validate_source_path(source_path, target)
    source = tomllib.loads(source_path.read_text())
    text = selected_usage_config(source, accounts)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = target.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
    temp.replace(target)
    return target
