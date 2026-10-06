import json
import subprocess  # nosec B404 # exercise local CLI scripts without a shell.
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from bridge.sources import write_usage_config

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
        self.config = self.directory / "desk.json"
        self.config.write_text(json.dumps({"token": "t" * 32, "port": 9876}))
        self.device = self.directory / "device.json"
        password = "test-password"  # nosec B105 # synthetic fixture, not a credential.
        self.device.write_text(
            json.dumps({"ssid": "test-network", "password": password, "host": "test-mac.local"})
        )

    def tearDown(self):
        self.temp.cleanup()

    def setup_command(self, *args, config=None, source=None, input_text=None):
        return subprocess.run(  # nosec B603 # this checkout's CLI with temporary test config.
            [
                sys.executable,
                str(ROOT / "scripts/create_config.py"),
                "--config",
                str(self.config if config is None else config),
                "--source-config",
                str(self.source if source is None else source),
                *args,
            ],
            capture_output=True,
            input=input_text,
            text=True,
            timeout=10,
            check=False,
        )

    def bridge_command(self, config):
        return subprocess.run(  # nosec B603 # local bridge with a test-owned collector and config.
            [sys.executable, str(ROOT / "bridge/server.py"), "--config", str(config), "--once"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def directory_contents(self):
        contents = {}
        for path in self.directory.rglob("*"):
            if path.is_symlink():
                value = path.readlink()
            elif path.is_file():
                value = path.read_bytes()
            else:
                value = None
            contents[path.relative_to(self.directory)] = (path.lstat().st_mode, value)
        return contents

    def collector_config(self):
        collector = self.directory / "collector"
        document = {"schema_version": 1, "entries": []}
        collector.write_text(f"#!{sys.executable}\nprint({json.dumps(document)!r})\n")
        collector.chmod(0o700)
        return {
            "token": "t" * 32,
            "accounts": [{"id": "anthropic@beta", "label": "z1"}],
            "source_config": str(self.source),
            "ai_usagebar": str(collector),
        }

    def assert_source_conflict(self, command, config, source):
        before = self.directory_contents()
        result = (
            self.setup_command(
                "--ssid",
                "test-network",
                "--host",
                "test-mac.local",
                "--account",
                "anthropic@beta=z1",
                config=config,
                source=source,
                input_text="synthetic-password\n",
            )
            if command == "setup"
            else self.bridge_command(config)
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("conflicts", result.stderr)
        self.assertNotIn("Wi-Fi password", result.stderr)
        self.assertEqual(self.directory_contents(), before)

    def test_custom_filename_preserves_network_and_token_and_cli_reads_config(self):
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
        self.assertEqual([a["label"] for a in selected["anthropic"]["accounts"]], ["beta"])
        for path in (self.config, self.device, self.directory / "ai-usagebar.toml"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        collector = self.directory / "collector"
        document = {"schema_version": 1, "entries": []}
        collector.write_text(f"#!{sys.executable}\nprint({json.dumps(document)!r})\n")
        collector.chmod(0o700)
        config["ai_usagebar"] = str(collector)
        self.config.write_text(json.dumps(config))
        result = self.bridge_command(self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["accounts"]), 1)
        self.assertEqual(payload["accounts"][0]["label"], "z1")
        self.assertTrue(payload["accounts"][0]["stale"])
        self.assertEqual(payload["display"], config["display"])

    def test_conflicting_config_names_are_rejected_before_any_writes(self):
        config = self.collector_config()
        for command in ("setup", "bridge"):
            for name in (
                "device.json",
                "ai-usagebar.toml",
                "snapshot.json",
                "device.tmp",
                "ai-usagebar.tmp",
                "snapshot.tmp",
            ):
                with self.subTest(command=command, name=name):
                    directory = self.directory / f"{command}-{name}"
                    directory.mkdir()
                    path = directory / name
                    if command == "bridge":
                        path.write_text(json.dumps(config))
                    before = self.directory_contents()
                    result = (
                        self.setup_command(
                            "--ssid",
                            "test-network",
                            "--host",
                            "test-mac.local",
                            "--account",
                            "anthropic@beta=z1",
                            config=path,
                            input_text="synthetic-password\n",
                        )
                        if command == "setup"
                        else self.bridge_command(path)
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("conflicts", result.stderr)
                    self.assertEqual(self.directory_contents(), before)

    def test_config_aliases_to_generated_files_are_rejected_without_changes(self):
        config = self.collector_config()
        for command in ("setup", "bridge"):
            for alias in ("config-symlink", "output-symlink", "hardlink"):
                with self.subTest(command=command, alias=alias):
                    directory = self.directory / f"{command}-{alias}"
                    directory.mkdir()
                    path = directory / "custom.json"
                    output = directory / "snapshot.json"
                    if alias == "config-symlink":
                        output.write_text(json.dumps(config))
                        path.symlink_to(output)
                    else:
                        path.write_text(json.dumps(config))
                        if alias == "output-symlink":
                            output.symlink_to(path)
                        else:
                            output.hardlink_to(path)
                    before = self.directory_contents()
                    result = (
                        self.setup_command(
                            "--ssid",
                            "test-network",
                            "--host",
                            "test-mac.local",
                            config=path,
                            input_text="synthetic-password\n",
                        )
                        if command == "setup"
                        else self.bridge_command(path)
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("conflicts", result.stderr)
                    self.assertEqual(self.directory_contents(), before)

    def test_case_variant_conflict_is_rejected_before_creating_directory_or_prompting(self):
        path = self.directory / "missing" / "DEVICE.JSON"
        before = self.directory_contents()
        result = self.setup_command(
            "--ssid",
            "test-network",
            "--host",
            "test-mac.local",
            "--account",
            "anthropic@beta=z1",
            config=path,
            input_text="",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts", result.stderr)
        self.assertNotIn("Wi-Fi password", result.stderr)
        self.assertEqual(self.directory_contents(), before)

    def test_dangling_case_variant_output_symlink_is_rejected_before_writes(self):
        directory = self.directory / "dangling"
        directory.mkdir()
        path = directory / "custom.json"
        (directory / "device.tmp").symlink_to("CUSTOM.JSON")
        before = self.directory_contents()
        result = self.setup_command(
            "--ssid",
            "test-network",
            "--host",
            "test-mac.local",
            "--account",
            "anthropic@beta=z1",
            config=path,
            input_text="synthetic-password\n",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts", result.stderr)
        self.assertNotIn("Wi-Fi password", result.stderr)
        self.assertEqual(self.directory_contents(), before)

    def test_source_overlapping_an_output_is_rejected_without_changes(self):
        config = self.collector_config()
        for command in ("setup", "bridge"):
            for index, name in enumerate(
                (
                    "custom.json",
                    "custom.tmp",
                    "device.json",
                    "device.tmp",
                    "ai-usagebar.toml",
                    "ai-usagebar.tmp",
                    "snapshot.json",
                    "snapshot.tmp",
                    "AI-USAGEBAR.TOML",
                )
            ):
                with self.subTest(command=command, name=name):
                    directory = self.directory / f"source-{command}-{index}"
                    directory.mkdir()
                    path = directory / "custom.json"
                    source = directory / name
                    source.write_bytes(self.source.read_bytes())
                    if command == "bridge":
                        path.write_text(json.dumps(config | {"source_config": str(source)}))
                    self.assert_source_conflict(command, path, source)

    def test_source_aliases_to_outputs_are_rejected_without_changes(self):
        config = self.collector_config()
        for command in ("setup", "bridge"):
            for alias in ("source-symlink", "output-symlink", "hardlink"):
                with self.subTest(command=command, alias=alias):
                    directory = self.directory / f"source-{command}-{alias}"
                    directory.mkdir()
                    path = directory / "custom.json"
                    source = directory / "original.toml"
                    output = directory / "ai-usagebar.tmp"
                    if alias == "source-symlink":
                        output.write_bytes(self.source.read_bytes())
                        source.symlink_to(output)
                    else:
                        source.write_bytes(self.source.read_bytes())
                        if alias == "output-symlink":
                            output.symlink_to(source.name.upper())
                        else:
                            output.hardlink_to(source)
                    path.write_text(json.dumps(config | {"source_config": str(source)}))
                    self.assert_source_conflict(command, path, source)

    def test_direct_source_writer_rejects_its_output_and_temporary_file(self):
        for index, name in enumerate(("ai-usagebar.toml", "ai-usagebar.tmp", "AI-USAGEBAR.TOML")):
            with self.subTest(name=name):
                directory = self.directory / f"writer-{index}"
                directory.mkdir()
                source = directory / name
                source.write_bytes(self.source.read_bytes())
                before = self.directory_contents()
                with self.assertRaisesRegex(ValueError, "conflicts"):
                    write_usage_config(
                        source,
                        directory / "ai-usagebar.toml",
                        [{"id": "anthropic@beta", "label": "z1"}],
                    )
                self.assertEqual(self.directory_contents(), before)

    def test_saved_source_conflict_is_rejected_without_changes(self):
        source = self.directory / "ai-usagebar.tmp"
        source.write_bytes(self.source.read_bytes())
        self.config.write_text(json.dumps(self.collector_config() | {"source_config": str(source)}))
        before = self.directory_contents()
        result = subprocess.run(  # nosec B603 # local setup reads only this temporary config.
            [sys.executable, str(ROOT / "scripts/create_config.py"), "--config", str(self.config)],
            capture_output=True,
            input="",
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("conflicts", result.stderr)
        self.assertNotIn("Wi-Fi password", result.stderr)
        self.assertEqual(self.directory_contents(), before)

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
            result.stdout.splitlines(), ["openai", "anthropic", "anthropic@alpha", "anthropic@beta"]
        )
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.directory / "ai-usagebar.toml").exists())


if __name__ == "__main__":
    unittest.main()
