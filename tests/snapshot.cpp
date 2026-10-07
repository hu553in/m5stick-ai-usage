#include "../firmware/src/quota_snapshot.h"
#include <assert.h>
#include <fstream>

JsonDocument fixture(size_t count) {
  JsonDocument doc;
  doc["schema"] = 1;
  doc["server_time"] = int64_t(1791302400);
  doc["max_age"] = 300;
  auto display = doc["display"].to<JsonObject>();
  display["poll_seconds"] = 30;
  display["offline_seconds"] = 90;
  display["brightness"] = 64;
  display["warning_percent"] = 70;
  display["critical_percent"] = 90;
  auto rows = doc["accounts"].to<JsonArray>();
  for (size_t i = 0; i < count; ++i) {
    auto row = rows.add<JsonObject>();
    row["id"] = std::string("source-") + std::to_string(i);
    row["label"] = std::string("a") + std::to_string(i);
    row["stale"] = false;
    row["fetched_at"] = int64_t(1791302400);
    row["short"]["used"] = 10 * i;
    row["short"]["reset_at"] = int64_t(1791310000);
    row["week"] = nullptr;
  }
  // JsonDocument owns its storage and returns by value; no local pointer escapes.
  // NOLINTNEXTLINE(clang-analyzer-core.StackAddressEscape)
  return doc;
}

void expectSnapshot(const JsonDocument &doc, QuotaSnapshot &snapshot, bool expected) {
  const bool actual = readSnapshot(doc, snapshot);
  assert(actual == expected);
}

int main(int argc, char **argv) {
  QuotaSnapshot snapshot;
  for (size_t count = 1; count <= MAX_ACCOUNTS; ++count) {
    auto doc = fixture(count);
    expectSnapshot(doc, snapshot, true);
    assert(snapshot.count == count);
    assert(snapshot.display.brightness == 64);
    assert(snapshot.display.pollSeconds == 30);
    assert(snapshot.display.warningPercent == 70);
    assert(snapshot.accounts[count - 1].shortWindow.used == static_cast<float>(10 * (count - 1)));
    assert(!snapshot.accounts[0].week.present);
  }
  auto changed = fixture(1);
  changed["accounts"][0]["id"] = "another@account";
  changed["accounts"][0]["label"] = "z";
  expectSnapshot(changed, snapshot, true);
  assert(snapshot.count == 1);
  assert(!strcmp(snapshot.accounts[0].id, "another@account"));
  assert(!strcmp(snapshot.accounts[0].label, "z"));

  // A bad last row must not replace any part of the retained snapshot.
  auto bad = fixture(3);
  bad["accounts"][2]["label"] = "too-long";
  expectSnapshot(bad, snapshot, false);
  assert(snapshot.count == 1);
  assert(!strcmp(snapshot.accounts[0].id, "another@account"));
  for (size_t count : {0, 4}) {
    auto doc = fixture(count);
    expectSnapshot(doc, snapshot, false);
  }
  bad = fixture(2);
  bad["accounts"][1]["id"] = "source-0";
  expectSnapshot(bad, snapshot, false);
  bad = fixture(2);
  bad["accounts"][1]["label"] = "a0";
  expectSnapshot(bad, snapshot, false);
  for (const char *label : {"*", "a b", "", "я"}) {
    bad = fixture(1);
    bad["accounts"][0]["label"] = label;
    expectSnapshot(bad, snapshot, false);
  }
  bad = fixture(1);
  bad["display"]["brightness"] = 256;
  expectSnapshot(bad, snapshot, false);
  bad = fixture(1);
  bad["display"]["offline_seconds"] = 5;
  expectSnapshot(bad, snapshot, false);
  bad = fixture(1);
  bad["accounts"][0]["short"]["used"] = true;
  expectSnapshot(bad, snapshot, false);
  if (argc == 2) {
    std::ifstream stream(argv[1]);
    JsonDocument doc;
    const auto error = deserializeJson(doc, stream);
    assert(!error);
    expectSnapshot(doc, snapshot, true);
    // The bridge integration test serializes real Python Snapshot output.
    assert(snapshot.count == 3);
    assert(snapshot.serverTime == 1700000060);
    assert(snapshot.maxAge == 900);
    assert(snapshot.display.pollSeconds == 30);
    assert(snapshot.display.offlineSeconds == 90);
    assert(snapshot.display.brightness == 64);
    assert(snapshot.display.warningPercent == 70);
    assert(snapshot.display.criticalPercent == 90);

    const auto &failed = snapshot.accounts[0];
    assert(!strcmp(failed.id, "anthropic@beta"));
    assert(!strcmp(failed.label, "b1"));
    assert(failed.stale && failed.fetched == 0);
    assert(!failed.shortWindow.present && !failed.week.present);

    const auto &codex = snapshot.accounts[1];
    assert(!strcmp(codex.id, "openai"));
    assert(!strcmp(codex.label, "o1"));
    assert(!codex.stale && codex.fetched == 1700000000);
    assert(codex.shortWindow.present && codex.shortWindow.used == 26);
    assert(codex.shortWindow.reset == 1700003600);
    assert(codex.week.present && codex.week.used == 98);
    assert(codex.week.reset == 1700086400);

    const auto &claude = snapshot.accounts[2];
    assert(!strcmp(claude.id, "anthropic@alpha"));
    assert(!strcmp(claude.label, "a1"));
    assert(!claude.stale && claude.fetched == 1700000000);
    assert(claude.shortWindow.present && claude.shortWindow.used == 0);
    assert(claude.shortWindow.reset == 0);
    assert(!claude.week.present);
  }
}
