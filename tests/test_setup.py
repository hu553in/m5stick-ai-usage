import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source = self.directory / "original.toml"
        self.source.write_text("""[[anthropic.accounts]]
label = "alpha"
credentials_path = "~/alpha/.credentials.json"
[[anthropic.accounts]]
label = "beta"
credentials_path = "~/beta/.credentials.json"
""")
        self.config = self.directory / "bridge.json"
        self.config.write_text(json.dumps({"token": "t" * 32, "port": 9876}))
        self.device = self.directory / "device.json"
        self.device.write_text(
            json.dumps(
                {
                    "ssid": "test-network",
                    "password": "test-password",
                    "host": "test-mac.local",
                }
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def setup_command(self, *args):
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/create_config.py"),
                "--config",
                str(self.config),
                "--source-config",
                str(self.source),
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_account_update_preserves_network_and_token_and_cli_reads_config(self):
        original = self.source.read_bytes()
        result = self.setup_command("--account", "anthropic@beta=z1")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(self.config.read_text())
        self.assertEqual(config["accounts"], [{"id": "anthropic@beta", "label": "z1"}])
        self.assertEqual(config["token"], "t" * 32)
        device = json.loads(self.device.read_text())
        self.assertEqual(device["ssid"], "test-network")
        self.assertEqual(device["password"], "test-password")
        self.assertEqual(device["host"], "test-mac.local")
        self.assertEqual(device["port"], 9876)
        self.assertEqual(device["token"], config["token"])
        self.assertEqual(self.source.read_bytes(), original)
        selected = tomllib.loads((self.directory / "ai-usagebar.toml").read_text())
        self.assertEqual(
            [a["label"] for a in selected["anthropic"]["accounts"]], ["beta"]
        )
        for path in (self.config, self.device, self.directory / "ai-usagebar.toml"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        collector = self.directory / "collector"
        document = {"schema_version": 1, "entries": []}
        collector.write_text(f"#!{sys.executable}\nprint({json.dumps(document)!r})\n")
        collector.chmod(0o700)
        config["ai_usagebar"] = str(collector)
        self.config.write_text(json.dumps(config))
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bridge/server.py"),
                "--config",
                str(self.config),
                "--once",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["accounts"]), 1)
        self.assertEqual(payload["accounts"][0]["label"], "z1")
        self.assertTrue(payload["accounts"][0]["stale"])
        self.assertEqual(payload["display"], config["display"])

    def test_invalid_account_does_not_modify_any_settings(self):
        before = {p: p.read_bytes() for p in (self.config, self.device, self.source)}
        result = self.setup_command("--account", "anthropic@missing=m1")
        self.assertNotEqual(result.returncode, 0)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.directory / "ai-usagebar.toml").exists())

    def test_list_accounts_is_read_only(self):
        before = self.config.read_bytes()
        result = self.setup_command("--list-accounts")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["openai", "anthropic", "anthropic@alpha", "anthropic@beta"],
        )
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.directory / "ai-usagebar.toml").exists())


if __name__ == "__main__":
    unittest.main()
