#pragma once
#include <ArduinoJson.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

// Capacity of the fixed, single-screen layout, not a configured account count.
constexpr size_t MAX_ACCOUNTS = 3;

struct Window {
  bool present = false;
  float used = 0;
  int64_t reset = 0;
};

struct Account {
  char id[129] = {};
  char label[3] = {};
  Window shortWindow;
  Window week;
  int64_t fetched = 0;
  bool stale = true;
};

struct DisplaySettings {
  // Bootstrap defaults until the first authenticated bridge response.
  int pollSeconds = 15;
  int offlineSeconds = 45;
  int brightness = 160;
  int warningPercent = 80;
  int criticalPercent = 95;
};

struct QuotaSnapshot {
  Account accounts[MAX_ACCOUNTS];
  size_t count = 0;
  int64_t serverTime = 0;
  int64_t maxAge = 300;
  DisplaySettings display;
};

inline bool readInteger(JsonVariantConst value, int low, int high, int &out) {
  if (!value.is<int>() || value.is<bool>())
    return false;
  int parsed = value.as<int>();
  if (parsed < low || parsed > high)
    return false;
  out = parsed;
  return true;
}

inline bool readWindow(JsonVariantConst value, Window &out) {
  out = Window{};
  if (value.isNull())
    return true;
  if (!value.is<JsonObjectConst>() || !value["used"].is<float>() || value["used"].is<bool>())
    return false;
  float used = value["used"].as<float>();
  if (!isfinite(used) || used < 0 || used > 100)
    return false;
  auto reset = value["reset_at"];
  if (!reset.isNull() && (!reset.is<int64_t>() || reset.is<bool>()))
    return false;
  out.present = true;
  out.used = used;
  out.reset = reset.isNull() ? 0 : reset.as<int64_t>();
  return out.reset >= 0;
}

inline bool readSnapshot(const JsonDocument &doc, QuotaSnapshot &out) {
  if (!doc["schema"].is<int>() || doc["schema"].as<int>() != 1 ||
      !doc["server_time"].is<int64_t>() || !doc["max_age"].is<int64_t>() ||
      !doc["accounts"].is<JsonArrayConst>())
    return false;
  QuotaSnapshot next;
  next.serverTime = doc["server_time"].as<int64_t>();
  next.maxAge = doc["max_age"].as<int64_t>();
  if (next.serverTime < 1700000000 || next.maxAge < 30 || next.maxAge > 3600)
    return false;
  auto display = doc["display"];
  if (!display.is<JsonObjectConst>() ||
      !readInteger(display["poll_seconds"], 5, 300, next.display.pollSeconds) ||
      !readInteger(display["offline_seconds"], next.display.pollSeconds, 3600,
                   next.display.offlineSeconds) ||
      !readInteger(display["brightness"], 1, 255, next.display.brightness) ||
      !readInteger(display["warning_percent"], 0, 100, next.display.warningPercent) ||
      !readInteger(display["critical_percent"], next.display.warningPercent, 100,
                   next.display.criticalPercent))
    return false;
  next.count = doc["accounts"].size();
  if (next.count < 1 || next.count > MAX_ACCOUNTS)
    return false;
  for (size_t i = 0; i < next.count; ++i) {
    JsonObjectConst row = doc["accounts"][i];
    const char *id = row["id"];
    const char *label = row["label"];
    if (!id || !label)
      return false;
    size_t length = strlen(id);
    if (length < 1 || length >= sizeof(next.accounts[i].id) || id[0] == ' ' ||
        id[length - 1] == ' ')
      return false;
    for (size_t j = 0; j < length; ++j) {
      if (id[j] < 32 || id[j] > 126)
        return false;
    }
    length = strlen(label);
    if (length < 1 || length >= sizeof(next.accounts[i].label))
      return false;
    for (size_t j = 0; j < length; ++j) {
      char c = label[j];
      if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')))
        return false;
    }
    for (size_t j = 0; j < i; ++j) {
      if (!strcmp(id, next.accounts[j].id) || !strcmp(label, next.accounts[j].label))
        return false;
    }
    auto &account = next.accounts[i];
    // Lengths were checked against these buffers before the copy.
    memcpy(account.id, id, strlen(id) + 1);
    memcpy(account.label, label, length + 1);
    if (!row["stale"].is<bool>() || row["short"].isUnbound() || row["week"].isUnbound())
      return false;
    if (!readWindow(row["short"], account.shortWindow) || !readWindow(row["week"], account.week))
      return false;
    if (!row["fetched_at"].isNull() && !row["fetched_at"].is<int64_t>())
      return false;
    account.fetched = row["fetched_at"] | int64_t(0);
    if (account.fetched < 0)
      return false;
    account.stale = row["stale"].as<bool>();
  }
  out = next;
  return true;
}
