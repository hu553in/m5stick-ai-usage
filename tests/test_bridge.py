import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import tomllib
from bridge.config import DEFAULT_DISPLAY, accounts_config, validate_config
from bridge.server import Snapshot, handler_for, normalize, timestamp
from bridge.sources import selected_usage_config

FETCHED = "2026-10-06T13:20:00Z"
ACCOUNTS = [
    {"id": "openai", "label": "o1"},
    {"id": "anthropic@alpha", "label": "a1"},
    {"id": "anthropic@beta", "label": "b1"},
]


def document():
    return {
        "schema_version": 1,
        "entries": [
            {
                "id": account["id"],
                "status": "ready",
                "error": None,
                "stale": False,
                "fetched_at": FETCHED,
                "email": "must-not-leave-the-mac@example.com",
                "metrics": [
                    {
                        "label": "Weekly (7d)",
                        "window_secs": 604800,
                        "percent": 98,
                        "reset_at": "2026-10-07T01:00:00Z",
                    },
                    {
                        "label": "Session (5h)",
                        "window_secs": 18000,
                        "percent": 26,
                        "reset_at": "2026-10-06T15:00:00Z",
                    },
                    {
                        "label": "Fable (7d)",
                        "window_secs": 604800,
                        "percent": 11,
                        "reset_at": "2026-10-07T01:00:00Z",
                    },
                ],
            }
            for account in ACCOUNTS
        ],
    }


class QuotaTests(unittest.TestCase):
    def test_source_config_does_not_fetch_default_or_copy_unrelated_secrets(self):
        source = {
            "anthropic": {
                "accounts": [
                    {
                        "label": "alpha",
                        "credentials_path": "~/Some Folder/alpha/.credentials.json",
                    },
                    {"label": "beta", "credentials_path": "~/beta/.credentials.json"},
                    {
                        "label": "unused",
                        "credentials_path": "~/unused/.credentials.json",
                    },
                ]
            },
            "zai": {"api_key": "unrelated-key"},
        }
        original = copy.deepcopy(source)
        text = selected_usage_config(source, ACCOUNTS)
        selected = tomllib.loads(text)
        self.assertFalse(selected["anthropic"]["show_default_account"])
        self.assertEqual(
            [a["label"] for a in selected["anthropic"]["accounts"]],
            ["alpha", "beta"],
        )
        self.assertTrue(selected["openai"]["enabled"])
        self.assertFalse(selected["zai"]["enabled"])
        self.assertNotIn("unrelated-key", text)
        self.assertEqual(source, original)

    def test_upstream_backoff_marks_only_its_account_stale(self):
        data = document()
        data["entries"][1]["stale"] = True
        self.store.update(data, self.now)
        rows = self.store.read(self.now)["accounts"]
        self.assertFalse(rows[0]["stale"])
        self.assertTrue(rows[1]["stale"])
        self.assertFalse(rows[2]["stale"])
        self.assertEqual(rows[1]["week"]["used"], 98)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Snapshot(Path(self.temp.name) / "snapshot.json", ACCOUNTS)
        self.now = timestamp(FETCHED)

    def tearDown(self):
        self.temp.cleanup()

    def test_named_accounts_only_and_no_identity_leaks(self):
        data = document()
        duplicate = copy.deepcopy(data["entries"][1])
        duplicate["id"] = "anthropic"
        data["entries"].append(duplicate)
        data["entries"].append({"id": "zai", "error": "no key"})
        self.store.update(data, self.now)
        result = self.store.read(self.now)
        self.assertEqual([r["label"] for r in result["accounts"]], ["o1", "a1", "b1"])
        self.assertNotIn("example.com", json.dumps(result))
        self.assertNotIn("metrics", json.dumps(result))
        self.assertFalse(any(r["stale"] for r in result["accounts"]))

    def test_missing_window_is_not_zero(self):
        data = document()
        data["entries"][0]["metrics"] = data["entries"][0]["metrics"][:1]
        self.store.update(data, self.now)
        self.assertIsNone(self.store.read(self.now)["accounts"][0]["short"])

    def test_zero_without_active_reset_is_valid(self):
        data = document()
        data["entries"][2]["metrics"][1].update(percent=0, reset_at=None)
        self.store.update(data, self.now)
        self.assertEqual(
            self.store.read(self.now)["accounts"][2]["short"],
            {"used": 0, "reset_at": None},
        )

    def test_model_specific_weekly_is_not_substituted(self):
        data = document()
        data["entries"][0]["metrics"] = data["entries"][0]["metrics"][2:]
        self.store.update(data, self.now)
        self.assertIsNone(self.store.read(self.now)["accounts"][0]["week"])

    def test_one_provider_error_retains_values_without_breaking_others(self):
        self.store.update(document(), self.now)
        data = document()
        data["entries"][0]["error"] = "auth failure"
        data["entries"][1]["metrics"][1]["percent"] = 27
        self.store.update(data, self.now)
        rows = self.store.read(self.now)["accounts"]
        self.assertEqual(rows[0]["short"]["used"], 26)
        self.assertTrue(rows[0]["stale"])
        self.assertFalse(rows[1]["stale"])
        self.assertEqual(rows[1]["short"]["used"], 27)

    def test_invalid_percentage_keeps_previous_snapshot(self):
        self.store.update(document(), self.now)
        for invalid in [True, "98", float("nan"), -1, 101]:
            with self.subTest(invalid=invalid):
                data = document()
                data["entries"][0]["metrics"][0]["percent"] = invalid
                self.store.update(data, self.now)
                row = self.store.read(self.now)["accounts"][0]
                self.assertTrue(row["stale"])
                self.assertEqual(row["week"]["used"], 98)

    def test_staleness_uses_source_timestamp(self):
        self.store.update(document(), self.now)
        self.assertTrue(
            all(r["stale"] for r in self.store.read(self.now + 301)["accounts"])
        )

    def test_expired_reset_does_not_clear_used_percentage(self):
        self.store.update(document(), self.now)
        rows = self.store.read(timestamp("2026-10-08T00:00:00Z"))["accounts"]
        self.assertEqual(rows[0]["week"]["used"], 98)

    def test_restart_retains_values_marked_stale(self):
        self.store.update(document(), self.now)
        restarted = Snapshot(self.store.cache, ACCOUNTS)
        row = restarted.read(self.now)["accounts"][0]
        self.assertTrue(row["stale"])
        self.assertEqual(row["week"]["used"], 98)

    def test_unsupported_schema_rejected(self):
        with self.assertRaises(ValueError):
            normalize({"schema_version": 2, "entries": []}, [], self.now, ACCOUNTS)

    def test_configuration_controls_count_order_and_labels(self):
        for count in range(1, 4):
            selected = list(reversed(ACCOUNTS))[:count]
            selected = [row | {"label": f"r{i}"} for i, row in enumerate(selected)]
            store = Snapshot(Path(self.temp.name) / f"rows-{count}.json", selected)
            data = document()
            for i, row in enumerate(data["entries"]):
                row["metrics"][1]["percent"] = i * 10
            store.update(data, self.now)
            rows = store.read(self.now)["accounts"]
            self.assertEqual(
                [row["id"] for row in rows], [row["id"] for row in selected]
            )
            self.assertEqual(
                [row["label"] for row in rows], [f"r{i}" for i in range(count)]
            )
            self.assertEqual(
                [row["short"]["used"] for row in rows], [20, 10, 0][:count]
            )

    def test_cache_reconfiguration_retains_by_id_and_uses_new_labels(self):
        self.store.update(document(), self.now)
        selected = [
            {"id": "anthropic@new", "label": "n1"},
            ACCOUNTS[2] | {"label": "b2"},
            ACCOUNTS[0] | {"label": "o2"},
        ]
        restarted = Snapshot(self.store.cache, selected)
        rows = restarted.read(self.now)["accounts"]
        self.assertIsNone(rows[0]["week"])
        self.assertEqual(rows[1]["week"]["used"], 98)
        self.assertEqual(rows[2]["label"], "o2")
        restarted.update({"schema_version": 1, "entries": []}, self.now)
        self.assertEqual(
            [r["label"] for r in restarted.read(self.now)["accounts"]],
            ["n1", "b2", "o2"],
        )

    def test_display_settings_are_sent_without_secrets(self):
        display = DEFAULT_DISPLAY | {"brightness": 64, "warning_percent": 70}
        store = Snapshot(self.store.cache, ACCOUNTS, max_age=900, display=display)
        payload = store.read(self.now)
        self.assertEqual(payload["display"], display)
        self.assertEqual(payload["max_age"], 900)
        self.assertNotIn("token", payload)

    def test_invalid_account_configuration_is_rejected(self):
        for rows in [
            [],
            ACCOUNTS * 2,
            [ACCOUNTS[0]] * 2,
            [{"id": "openai", "label": "long"}],
            [{"id": "openai", "label": "я"}],
            [ACCOUNTS[0], ACCOUNTS[1] | {"label": "o1"}],
        ]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                accounts_config(rows)

    def test_invalid_runtime_settings_are_rejected(self):
        base = {"token": "x" * 32, "accounts": ACCOUNTS}
        for changes in [
            {"port": True},
            {"port": 0},
            {"poll_seconds": 1},
            {"display": {"brightness": 256}},
            {"display": {"warning_percent": 99, "critical_percent": 80}},
            {"display": {"poll_seconds": 60, "offline_seconds": 15}},
            {"display": {"typo": 1}},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_config(base | changes)

    def test_sources_can_select_only_codex_or_default_claude(self):
        codex = tomllib.loads(selected_usage_config({}, [ACCOUNTS[0]]))
        self.assertFalse(codex["anthropic"]["enabled"])
        self.assertTrue(codex["openai"]["enabled"])
        claude = tomllib.loads(
            selected_usage_config(
                {"anthropic": {"credentials_path": "~/custom/.credentials.json"}},
                [{"id": "anthropic", "label": "a"}],
            )
        )
        self.assertFalse(claude["openai"]["enabled"])
        self.assertTrue(claude["anthropic"]["show_default_account"])
        self.assertEqual(
            claude["anthropic"]["credentials_path"], "~/custom/.credentials.json"
        )
        with self.assertRaises(ValueError):
            selected_usage_config({}, [{"id": "anthropic@missing", "label": "m"}])

    def test_discovered_account_paths_and_explicit_overrides(self):
        directory = Path(self.temp.name) / "profiles"
        (directory / "discovered").mkdir(parents=True)
        (directory / "override").mkdir()
        source = {
            "anthropic": {
                "accounts_dir": str(directory),
                "accounts": [
                    {
                        "label": "override",
                        "credentials_path": "~/профили/📁/.credentials.json",
                    }
                ],
            }
        }
        selected = tomllib.loads(
            selected_usage_config(
                source,
                [
                    {"id": "anthropic@discovered", "label": "d"},
                    {"id": "anthropic@override", "label": "o"},
                ],
            )
        )
        rows = selected["anthropic"]["accounts"]
        self.assertEqual(
            rows[0]["credentials_path"], str(directory / "discovered/.credentials.json")
        )
        self.assertEqual(rows[1]["credentials_path"], "~/профили/📁/.credentials.json")
        self.assertNotIn("accounts_dir", selected["anthropic"])

    def test_http_requires_device_token_and_returns_only_display_data(self):
        self.store.update(document(), self.now)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), handler_for(self.store, "test-token")
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/v1/status"
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url)
            self.assertEqual(error.exception.code, 401)
            request = urllib.request.Request(
                url, headers={"Authorization": "Bearer test-token"}
            )
            with urllib.request.urlopen(request) as response:
                body = response.read()
                self.assertEqual(int(response.headers["Content-Length"]), len(body))
                self.assertEqual(len(json.loads(body)["accounts"]), 3)
                self.assertNotIn(b"email", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
