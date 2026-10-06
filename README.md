# M5Stick AI usage

A Wi-Fi quota display for M5StickC Plus2. A Mac bridge reads selected Codex and Claude accounts
through the installed `ai-usagebar`; the device shows used percentages and reset countdowns on one
landscape screen.

<p align="center">
  <img src="docs/images/display.png" alt="Quota display captured from an M5StickC Plus2" />
</p>

Accounts, display labels and row order come from local configuration. The screen supports one to
three accounts, with one or two ASCII letters or digits per label. Each name is followed by a small
grey `· used`; `reset` aligns below `used`. The `5h` and `week` columns show percentages and
countdowns. Missing windows and reset times use the same bold dash. Zero remains a reported value.

## Configuration

[config.example.json](config.example.json) documents the editable settings with fictional account
names. Actual settings live outside this checkout in `~/.config/m5stick-ai-usage/`:

- `bridge.json`: selected account IDs and labels, network binding, port, collection interval,
  freshness limit, source paths, display preferences and generated access token.
- `device.json`: Wi-Fi credentials, bridge hostname, port and access token. These are provisioned
  into the device's NVS, never compiled into the firmware.
- `ai-usagebar.toml`: generated selection of the original ai-usagebar configuration. It references
  existing credentials; it does not copy tokens or modify the original configuration.
- `snapshot.json`: last known quotas, retained across bridge restarts.

The account list controls row order. Supported IDs are `openai`, `anthropic` (the default Claude
account) and `anthropic@NAME` for configured named Claude accounts. Explicit accounts and
`accounts_dir` discovery are supported. Other provider types are not supported by the source
selector.

The setup script generates the access token, so the example deliberately omits it. Do not replace an
existing local config with the example: edit the existing file to retain its token and selected
accounts.

| Setting                    | Default                           | Meaning                                                                        |
| -------------------------- | --------------------------------- | ------------------------------------------------------------------------------ |
| `accounts`                 | Required                          | 1–3 unique IDs with unique 1–2 character labels                                |
| `listen`, `port`           | `0.0.0.0`, `8765`                 | Bridge IPv4 binding and TCP port                                               |
| `poll_seconds`             | `120`                             | Mac collection interval, 60–3600 seconds                                       |
| `max_age`                  | `300`                             | Mark source data stale after 30–3600 seconds                                   |
| `ai_usagebar`              | Detected executable               | ai-usagebar binary path                                                        |
| `source_config`            | Standard macOS ai-usagebar config | Original source configuration, not the generated selection                     |
| `display.poll_seconds`     | `15`                              | Device snapshot interval, 5–300 seconds                                        |
| `display.offline_seconds`  | `45`                              | Maximum age of a successful bridge response; at least the device poll interval |
| `display.brightness`       | `160`                             | LCD brightness, 1–255                                                          |
| `display.warning_percent`  | `80`                              | Yellow percentage threshold                                                    |
| `display.critical_percent` | `95`                              | Red percentage threshold; at least the yellow threshold                        |

Edit `bridge.json` and restart the bridge to change accounts, order, labels or display preferences.
The bridge regenerates the source selection at startup and sends display settings with its
snapshots. Once this firmware is installed, these changes require neither rebuilding nor flashing.
Before the first snapshot, the firmware uses bootstrap timing and brightness defaults.

Changing Wi-Fi, bridge hostname, port or token also requires updating `device.json` and running
`scripts/device.py configure` over USB. Running setup again synchronizes the port and token while
preserving existing Wi-Fi credentials unless `--ssid` is supplied.

Hardware dimensions, the three-row capacity, the two quota windows, text layout and protocol
validation remain in the firmware. They describe this display rather than a particular user's
installation.

## Setup

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python scripts/create_config.py --list-accounts
```

Choose account IDs from the list. Default IDs are listed even if that provider is not logged in.
Named accounts must exist in the original ai-usagebar configuration. For example, replace
`YOUR_ACCOUNT` below with a listed account name:

```sh
.venv/bin/python scripts/create_config.py --ssid 'YOUR_WIFI_NAME' \
  --account 'openai=o1' --account 'anthropic@YOUR_ACCOUNT=a1'
```

Setup prompts for the Wi-Fi password, detects the Mac's Bonjour hostname and creates private local
config files. It reuses any existing device access token. To change the selected accounts later,
repeat `--account` for the complete new list and omit `--ssid` to keep Wi-Fi settings.
Alternatively, edit `accounts` in the existing `bridge.json` directly and restart the service.

Use `--source-config PATH` for an original ai-usagebar config in a different location and
`--host HOST` to override hostname detection. `--config PATH` selects a different bridge config;
generated files and `device.json` live beside it. Pass the same `--config` to the service installer,
bridge and hardware checker. For `device.py`, `--config` points to the adjacent **device.json**
instead.

If upgrading an older setup without an `accounts` field, run setup with the complete `--account`
list and `--source-config` pointing to the original ai-usagebar config. Existing Wi-Fi credentials
and token are retained.

## Build and flash

Connect the device with a USB data cable. Port detection works when there is one matching adapter;
otherwise use an explicit serial port.

```sh
.venv/bin/pio run
.venv/bin/pio run -t upload
.venv/bin/python scripts/device.py configure
.venv/bin/python scripts/device.py status
```

PlatformIO accepts `--upload-port PORT`; `device.py` accepts `--port PORT`. Subsequent uploads
retain the saved Wi-Fi settings. Erasing flash removes them.

The build uses the M5Stack `m5stick-c` base board with the Plus2's 8 MB partition layout. M5Unified
selects Plus2 hardware. The screen is 240×135 in landscape orientation. USB can supply power
independently of the Mac after setup.

## Bridge service

```sh
.venv/bin/python scripts/install_service.py
```

The macOS launch agent `local.m5stick-ai-usage` starts immediately, runs after login and restarts
after unexpected exits. It references this checkout, so reinstall after moving the project. Its
plist lives in `~/Library/LaunchAgents/`; logs are in `~/Library/Logs/m5stick-ai-usage/bridge.log`.

After editing `bridge.json`:

```sh
launchctl kickstart -k "gui/$(id -u)/local.m5stick-ai-usage"
```

To inspect the service, use `launchctl print "gui/$(id -u)/local.m5stick-ai-usage"`. To stop it, use
`launchctl bootout "gui/$(id -u)/local.m5stick-ai-usage"`. Running the installer starts it again.
Removing its plist also disables future login startup.

## Connection and stale data

The device uses 2.4 GHz Wi-Fi. The Mac can use either band of the same network if the router allows
communication. The firmware resolves the configured `.local` hostname through mDNS and resolves it
again after failures. A literal IPv4 address is also supported.

The bridge exposes authenticated quota data at `/v1/status` and a basic unauthenticated health
result at `/health`. It fetches only configured sources and respects ai-usagebar's cache and
rate-limit backoff.

On network failure, the display retains values and shows `offline`. A source refresh failure or
expired `max_age` shows `stale` with an asterisk beside the affected account. Timers continue
locally; a countdown reaching zero never invents a quota reset. After a device reboot, account rows
appear with the first valid bridge response. Button A requests a snapshot immediately. There are no
task screens or sound alerts.

## Diagnostics and checks

```sh
.venv/bin/python bridge/server.py --once
.venv/bin/python scripts/device.py status
.venv/bin/python scripts/device.py screenshot --output artifacts/display.png
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pio run
mkdir -p artifacts
c++ -std=c++17 -Wall -Wextra -Werror tests/countdown.cpp -o artifacts/countdown-test
./artifacts/countdown-test
c++ -std=c++17 -Wall -Wextra -Werror \
  -I.pio/libdeps/m5stickc-plus2/ArduinoJson/src \
  tests/snapshot.cpp -o artifacts/snapshot-test
./artifacts/snapshot-test
```

USB diagnostics never print credentials or the device token. Screenshots come from the framebuffer
sent to the LCD, not a photograph of the panel. `--once` fetches quotas; ordinary unit tests use
synthetic accounts and temporary files.

The Python tests cover configuration, source selection, account count/order/labels, cache retention
by ID, missing quotas, source failures and HTTP authentication. Native C++ tests exercise countdown
boundaries and the firmware's actual JSON parser, including dynamic rows, invalid settings and
retaining a complete snapshot after invalid input.

Run `.venv/bin/python scripts/verify_device.py` only with a connected, provisioned device and an
installed bridge. It reboots the device, reconnects Wi-Fi, briefly stops the bridge and tests
launchd restart. It restores the service after the outage and saves a report under `artifacts/`.
These hardware checks must be rerun after installing changed firmware; build and parser checks do
not replace them.

## References

- [M5StickC Plus2 hardware](https://docs.m5stack.com/en/core/StickC-Plus2)
- [M5Unified](https://github.com/m5stack/M5Unified)
- [ai-usagebar](https://github.com/akitaonrails/ai-usagebar)
