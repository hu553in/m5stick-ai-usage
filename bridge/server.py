"""Expose configured ai-usagebar quotas to the desk display."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hmac
import json
import logging
import math
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.config import DEFAULT_CONFIG, accounts_config, display_config, load_config
from bridge.sources import write_usage_config

LOG = logging.getLogger("m5-ai-usage")


def timestamp(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be ISO 8601")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    return int(parsed.timestamp())


def metric(entry: dict, seconds: int) -> dict | None:
    """Use account-wide windows; never substitute a model-specific weekly quota."""
    candidates = entry.get("metrics")
    if not isinstance(candidates, list):
        raise ValueError("metrics must be a list")
    for row in candidates:
        if not isinstance(row, dict):
            raise ValueError("invalid metric")
        if row.get("window_secs") != seconds:
            continue
        label = str(row.get("label", "")).lower()
        if seconds == 604800 and "weekly" not in label:
            continue
        percent = row.get("percent")
        if percent is None:
            return None
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            raise ValueError("invalid percentage")
        if not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("percentage outside quota range")
        return {"used": percent, "reset_at": timestamp(row.get("reset_at"))}
    return None


def empty_account(source_id: str, label: str) -> dict:
    return {
        "id": source_id,
        "label": label,
        "short": None,
        "week": None,
        "fetched_at": None,
        "stale": True,
        "error": "waiting",
    }


def normalize(
    document: dict, previous: list[dict], now: int, accounts: list[dict]
) -> list[dict]:
    if document.get("schema_version") != 1 or not isinstance(
        document.get("entries"), list
    ):
        raise ValueError("unsupported ai-usagebar document")
    entries = {e.get("id"): e for e in document["entries"] if isinstance(e, dict)}
    old = {e["id"]: e for e in previous}
    result = []
    for account in accounts:
        source_id, label = account["id"], account["label"]
        row = copy.deepcopy(old.get(source_id, empty_account(source_id, label)))
        entry = entries.get(source_id)
        error = "account unavailable"
        if (
            entry is not None
            and not entry.get("error")
            and entry.get("status") == "ready"
        ):
            try:
                fetched = timestamp(entry.get("fetched_at"))
                if fetched is None or fetched > now + 300:
                    raise ValueError("invalid fetch time")
                row = {
                    "id": source_id,
                    "label": label,
                    "short": metric(entry, 18000),
                    "week": metric(entry, 604800),
                    "fetched_at": fetched,
                    "stale": bool(entry.get("stale", False)),
                    "error": None,
                }
                result.append(row)
                continue
            except (ValueError, TypeError, OverflowError):
                error = "invalid quota data"
        row.update(label=label, stale=True, error=error)
        result.append(row)
    return result


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")
    tmp.replace(path)


class Snapshot:
    def __init__(
        self,
        cache: Path,
        accounts: list[dict],
        max_age: int = 300,
        display: dict | None = None,
    ):
        self.cache = cache
        self.max_age = max_age
        self.accounts = accounts_config(accounts)
        self.display = display_config({} if display is None else display)
        self.lock = threading.Lock()
        self.rows = [empty_account(row["id"], row["label"]) for row in self.accounts]
        # A restart retains values, but does not pretend they were just fetched.
        try:
            saved = json.loads(cache.read_text())
            if saved.get("schema") == 1:
                cached = {row["id"]: row for row in saved["accounts"]}
                for i, account in enumerate(self.accounts):
                    row = cached.get(account["id"], {})
                    if all(key in row for key in self.rows[i]):
                        self.rows[i] = row | {
                            "label": account["label"],
                            "stale": True,
                            "error": "refreshing",
                        }
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def update(self, document: dict, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        with self.lock:
            self.rows = normalize(document, self.rows, now, self.accounts)
            payload = {"schema": 1, "accounts": self.rows}
            atomic_json(self.cache, payload)

    def fail(self, error: str) -> None:
        with self.lock:
            for row in self.rows:
                row.update(stale=True, error=error)

    def read(self, now: int | None = None) -> dict:
        now = int(time.time()) if now is None else now
        with self.lock:
            rows = copy.deepcopy(self.rows)
        for row in rows:
            fetched = row["fetched_at"]
            if fetched is None or now - fetched > self.max_age or fetched > now + 300:
                row["stale"] = True
        return {
            "schema": 1,
            "server_time": now,
            "max_age": self.max_age,
            "display": self.display.copy(),
            "accounts": rows,
        }


def collect(binary: Path, snapshot: Snapshot, source_config: Path) -> None:
    try:
        result = subprocess.run(
            [str(binary), "--config", str(source_config), "usage", "--json"],
            capture_output=True,
            text=True,
            timeout=45,
            check=True,
        )
        document = json.loads(result.stdout)
        snapshot.update(document)
        errors = [r["label"] for r in snapshot.read()["accounts"] if r["error"]]
        if errors:
            LOG.warning("Quota unavailable for %s", ", ".join(errors))
    except subprocess.TimeoutExpired:
        snapshot.fail("collector timeout")
        LOG.warning("ai-usagebar timed out")
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        subprocess.CalledProcessError,
    ):
        snapshot.fail("collector error")
        LOG.error("Could not collect quota data")


def handler_for(snapshot: Snapshot, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args):
            pass

        def send_json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path == "/health":
                self.send_json(200, {"ok": True})
                return
            if self.path != "/v1/status":
                self.send_json(404, {"error": "not found"})
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                self.send_json(401, {"error": "unauthorized"})
                return
            self.send_json(200, snapshot.read())

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--once", action="store_true", help="Fetch and print display JSON, then exit"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config_path = args.config.expanduser().resolve()
    try:
        config = load_config(config_path)
        source_config = write_usage_config(
            Path(config["source_config"]),
            config_path.parent / "ai-usagebar.toml",
            config["accounts"],
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    token = config["token"]
    binary = Path(config["ai_usagebar"]).expanduser()
    snapshot = Snapshot(
        config_path.parent / "snapshot.json",
        config["accounts"],
        config["max_age"],
        config["display"],
    )
    if args.once:
        collect(binary, snapshot, source_config)
        print(json.dumps(snapshot.read(), indent=2))
        return
    interval = config["poll_seconds"]
    stop = threading.Event()
    server = ThreadingHTTPServer(
        (config["listen"], config["port"]),
        handler_for(snapshot, token),
    )
    server.daemon_threads = True

    def poll() -> None:
        while not stop.is_set():
            collect(binary, snapshot, source_config)
            stop.wait(interval)

    worker = threading.Thread(target=poll, name="quota-collector", daemon=True)
    worker.start()

    def shutdown(_signum, _frame):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    LOG.info("Listening on port %s; collecting every %ss", server.server_port, interval)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=46)


if __name__ == "__main__":
    main()
