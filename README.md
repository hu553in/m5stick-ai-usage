# M5Stick AI usage

[![CI](https://github.com/hu553in/m5stick-ai-usage/actions/workflows/ci.yml/badge.svg)](https://github.com/hu553in/m5stick-ai-usage/actions/workflows/ci.yml)

Codex and Claude usage-limit monitor for M5StickC Plus2. Shows used quota and time until reset for
up to three accounts, with separate five-hour and weekly windows. A local Mac bridge reads the
accounts through [ai-usagebar](https://github.com/akitaonrails/ai-usagebar) and sends snapshots to
the display over Wi-Fi.

<p align="center">
  <img src="docs/images/display.png" alt="Codex and Claude usage limits on an M5StickC Plus2" />
</p>

## Requirements

- M5StickC Plus2, a USB data cable for flashing and provisioning, and a 2.4 GHz Wi-Fi network.
- A Mac with ai-usagebar installed and authenticated for the CLI accounts to display.
- [uv](https://docs.astral.sh/uv/getting-started/installation/),
  [Bun](https://bun.sh/docs/installation), Node.js, Make and Clang. On macOS, install the Xcode
  Command Line Tools with `xcode-select --install`. Linux development checks require `clang` and
  `build-essential` on Debian/Ubuntu; the launch agent is macOS-specific.

## Setup

```sh
make install-deps
uv run --locked python scripts/create_config.py --list-accounts
```

Choose account IDs from the list. `openai` and `anthropic` are listed even if the corresponding
provider is not logged in. Named Claude accounts must exist in ai-usagebar's configuration:

```sh
uv run --locked python scripts/create_config.py --ssid 'YOUR_WIFI_NAME' \
  --account 'openai=o1' --account 'anthropic@YOUR_ACCOUNT=a1'
```

Replace `YOUR_ACCOUNT` with a listed name. Setup prompts for the Wi-Fi password, detects the Mac's
Bonjour hostname and creates private local config files. Existing Wi-Fi credentials and the device
token are retained unless explicitly changed. To replace the account list later, repeat `--account`
for every desired row and omit `--ssid`.

Use `--source-config PATH` for an ai-usagebar config outside its standard location and `--host HOST`
to override hostname detection. To store this project's configuration elsewhere, pass
`--config /path/to/bridge.json`; generated files and `device.json` live beside it. Pass the same
bridge config path to the service installer and hardware checker. For `device.py`, `--config`
selects **device.json** instead.

The bridge config cannot use the generated names `device.json`, `ai-usagebar.toml`, `snapshot.json`
or their `.tmp` names, regardless of case. The original ai-usagebar config must be separate from
this project's config, generated files and their `.tmp` paths. Setup and bridge startup reject
conflicting paths before writing files.

## Configuration

[config.example.json](config.example.json) lists editable settings with fictional account names.
Setup writes `bridge.json` and USB-provisioned `device.json` under `~/.config/m5stick-ai-usage/`. It
generates `ai-usagebar.toml` beside them, referencing existing CLI credentials without changing the
original source config. `snapshot.json` retains the last known quotas across bridge restarts.

Supported account IDs are `openai`, `anthropic` for default Claude, and `anthropic@NAME` for named
Claude accounts. Explicit accounts and `accounts_dir` discovery are supported. Select 1-3 unique
accounts; row order follows `accounts`, and labels are unique one- or two-character ASCII letters or
digits. Claude Desktop profile discovery is disabled so it cannot add or shadow these accounts.

Setup generates the access token, so the example omits it. Edit an existing `bridge.json` rather
than replacing it with the example and losing its token or account selection.

`poll_seconds` accepts 60-3600 seconds; `max_age` accepts 30-3600. Under `display`, `poll_seconds`
accepts 5-300 seconds and `offline_seconds` must be between that interval and 3600. Brightness is
1-255; percentage thresholds are 0-100, with `critical_percent` at least `warning_percent`.

Restart the bridge after changing accounts, labels, ordering or display settings. The device
receives these settings in its next snapshot, without rebuilding or flashing. Changing Wi-Fi, bridge
hostname, port or token also requires updating `device.json` and provisioning it over USB. Rerunning
setup synchronizes the port and token between the two config files.

## Build and flash

Build the firmware, then connect the device with a USB data cable to upload and provision it:

```sh
make build
uv run --locked pio run -t upload
uv run --locked python scripts/device.py configure
uv run --locked python scripts/device.py status
```

Port detection works with one matching USB adapter. Otherwise, pass `--upload-port PORT` to
PlatformIO and `--port PORT` to `device.py`. Uploading retains saved Wi-Fi settings; erasing flash
removes them.

The build uses the `m5stick-c` base board with the Plus2's 8 MB partition layout. M5Unified selects
the Plus2 hardware. The display is 240×135 in landscape orientation. After setup, USB power can come
from a charger instead of the Mac.

## Bridge service

```sh
uv run --locked python scripts/install_service.py
```

The launch agent `local.m5stick-ai-usage` starts immediately, runs after login and restarts after
unexpected exits. It references this checkout, so reinstall it after moving the project. The plist
is in `~/Library/LaunchAgents/`; logs are in `~/Library/Logs/m5stick-ai-usage/bridge.log`.

After editing `bridge.json`:

```sh
launchctl kickstart -k "gui/$(id -u)/local.m5stick-ai-usage"
```

Use `launchctl print "gui/$(id -u)/local.m5stick-ai-usage"` to inspect the service and
`launchctl bootout "gui/$(id -u)/local.m5stick-ai-usage"` to stop it. Running the installer starts
it again. Removing the plist disables future login startup.

## Display and connection

The `used` rows show percentages consumed; `reset` rows show time remaining. Missing quota windows
and reset times appear as dashes. Zero remains a reported value. Button A requests a fresh snapshot.

The device uses 2.4 GHz Wi-Fi; the Mac may use either band of the same network if the router permits
communication. The firmware resolves the configured `.local` hostname through mDNS and retries
resolution after failures. A literal IPv4 address also works.

The bridge serves authenticated usage data at `/v1/status` and an unauthenticated health result at
`/health`. It fetches only configured accounts and respects ai-usagebar's cache and rate-limit
backoff. On network failure, the display retains values and shows `offline`. Source failures or
expired data mark affected accounts with an asterisk and show `stale` while the bridge connection is
online. Countdowns continue locally; reaching zero does not invent a new quota window. After a
device reboot, rows appear with the first valid bridge response.

## Development

```sh
make check      # All checks, tests and firmware build
make check-fix  # Safe fixes followed by the full gate
```

For focused checks:

```sh
make lint
make check-types
make check-cpp
make test
make build
```

Install the Prek pre-commit and Conventional Commit hooks with `uv run --locked prek install`.
Pre-commit runs `make check-fix`; CI runs `make check` on Linux and macOS. These checks use
synthetic data and never flash the device, provision credentials or restart the bridge.

Native tests run with ASan/UBSan, including Python-produced snapshots consumed by the firmware's
actual parser. Python branch coverage includes setup subprocesses and reports untested USB/service
code; the hardware test runner is excluded. There is no coverage threshold.

Cppcheck analyzes owned sources without external SDK headers because it cannot parse ArduinoJson's
templates. clang-tidy analyzes the shared firmware headers through native tests; hardware-specific
`main.cpp` is checked by Cppcheck and the ESP32 compiler. These checks do not establish hardware
behavior. Narrow analyzer exceptions are documented beside their configuration or source line.

Renovate uses the shared Python/Bun presets and PlatformIO's registry. Its validator is fetched
separately by `bunx`. The `adm-zip` override supplies security fixes until the actionlint wrapper
accepts that dependency version.

## Diagnostics

```sh
uv run --locked python bridge/server.py --once
uv run --locked python scripts/device.py status
uv run --locked python scripts/device.py screenshot --output artifacts/display.png
```

`--once` fetches real usage data. USB diagnostics do not print credentials or the device token.
Screenshots come from the framebuffer sent to the LCD.

Run `uv run --locked python scripts/verify_device.py` only with a connected, provisioned device and
an installed bridge. It reboots the device, reconnects Wi-Fi, briefly stops the bridge and tests
launchd restart. It restores the service after the outage and saves a report under `artifacts/`.
Rerun these hardware checks after installing changed firmware.

## References

- [M5StickC Plus2 hardware](https://docs.m5stack.com/en/core/StickC-Plus2)
- [M5Unified](https://github.com/m5stack/M5Unified)
- [ai-usagebar](https://github.com/akitaonrails/ai-usagebar)
