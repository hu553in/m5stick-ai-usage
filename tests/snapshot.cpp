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
  return doc;
}

int main(int argc, char **argv) {
  QuotaSnapshot snapshot;
  for (size_t count = 1; count <= MAX_ACCOUNTS; ++count) {
    auto doc = fixture(count);
    assert(readSnapshot(doc, snapshot));
    assert(snapshot.count == count);
    assert(snapshot.display.brightness == 64);
    assert(snapshot.display.pollSeconds == 30);
    assert(snapshot.display.warningPercent == 70);
    assert(snapshot.accounts[count - 1].shortWindow.used == 10 * (count - 1));
    assert(!snapshot.accounts[0].week.present);
  }
  auto changed = fixture(1);
  changed["accounts"][0]["id"] = "another@account";
  changed["accounts"][0]["label"] = "z";
  assert(readSnapshot(changed, snapshot));
  assert(snapshot.count == 1);
  assert(!strcmp(snapshot.accounts[0].id, "another@account"));
  assert(!strcmp(snapshot.accounts[0].label, "z"));

  // A bad last row must not replace any part of the retained snapshot.
  auto bad = fixture(3);
  bad["accounts"][2]["label"] = "too-long";
  assert(!readSnapshot(bad, snapshot));
  assert(snapshot.count == 1);
  assert(!strcmp(snapshot.accounts[0].id, "another@account"));
  for (size_t count : {0, 4}) {
    auto doc = fixture(count);
    assert(!readSnapshot(doc, snapshot));
  }
  bad = fixture(2);
  bad["accounts"][1]["id"] = "source-0";
  assert(!readSnapshot(bad, snapshot));
  bad = fixture(2);
  bad["accounts"][1]["label"] = "a0";
  assert(!readSnapshot(bad, snapshot));
  for (const char *label : {"*", "a b", "", "я"}) {
    bad = fixture(1);
    bad["accounts"][0]["label"] = label;
    assert(!readSnapshot(bad, snapshot));
  }
  bad = fixture(1);
  bad["display"]["brightness"] = 256;
  assert(!readSnapshot(bad, snapshot));
  bad = fixture(1);
  bad["display"]["offline_seconds"] = 5;
  assert(!readSnapshot(bad, snapshot));
  bad = fixture(1);
  bad["accounts"][0]["short"]["used"] = true;
  assert(!readSnapshot(bad, snapshot));
  if (argc == 2) {
    std::ifstream stream(argv[1]);
    JsonDocument doc;
    assert(!deserializeJson(doc, stream));
    assert(readSnapshot(doc, snapshot));
    assert(snapshot.count == doc["accounts"].size());
  }
}
