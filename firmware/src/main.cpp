#include "quota_snapshot.h"
#include "quota_time.h"
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <M5Unified.h>
#include <Preferences.h>
#include <WiFi.h>

namespace {
constexpr char VERSION[] = "1.0.2";
constexpr uint32_t RETRY_MS = 15000;
constexpr uint16_t BG = 0x0862;
constexpr uint16_t TEXT = 0xEF7D;
constexpr uint16_t MUTED = 0x9D56;
constexpr uint16_t AMBER = 0xFDEC;
constexpr uint16_t RED = 0xFB2D;
constexpr int SHORT_COLUMN_X = 120;
constexpr int WEEK_COLUMN_X = 194;
struct Settings {
  String ssid;
  String password;
  String host;
  String token;
  uint16_t port = 8765;
  bool ready() const { return !ssid.isEmpty() && !host.isEmpty() && token.length() >= 32; }
} settings;

Preferences preferences;
M5Canvas frame(&M5.Display);
QuotaSnapshot snapshot;
IPAddress serverIP;
String serialLine;
String lastError = "waiting";
bool mdnsStarted = false;
bool gotSnapshot = false;
bool lastRequestOK = false;
uint32_t lastSuccess = 0;
uint32_t nextPoll = 0;
uint32_t lastJoin = 0;
uint32_t lastDraw = 0;
uint64_t clockMillis = 0;
uint32_t previousMillis = 0;
uint64_t timeAnchorMillis = 0;

int64_t nowSeconds() {
  return advanceTimestamp(snapshot.serverTime, clockMillis - timeAnchorMillis);
}

bool offline() {
  return !lastRequestOK || WiFi.status() != WL_CONNECTED || !gotSnapshot ||
         uint32_t(millis() - lastSuccess) > uint32_t(snapshot.display.offlineSeconds * 1000);
}

bool isStale(const Account &account) {
  return account.stale || account.fetched <= 0 || nowSeconds() - account.fetched > snapshot.maxAge;
}

void logEvent(const char *event) { Serial.printf("{\"event\":\"%s\"}\n", event); }

void drawWindow(const Window &window, int center, int top) {
  frame.setTextDatum(middle_center);
  frame.setFont(&fonts::FreeSansBold9pt7b);
  uint16_t color = window.used >= snapshot.display.criticalPercent  ? RED
                   : window.used >= snapshot.display.warningPercent ? AMBER
                                                                    : TEXT;
  frame.setTextColor(window.present ? color : MUTED, BG);
  char used[12];
  if (window.present)
    snprintf(used, sizeof(used), "%.0f%%", window.used);
  else
    snprintf(used, sizeof(used), "-");
  frame.drawString(used, center, top + 10);
  char countdown[COUNTDOWN_BUFFER_SIZE];
  formatCountdown(countdown, sizeof(countdown), window.present ? window.reset : 0, nowSeconds());
  if (countdown[0] == '-')
    frame.setFont(&fonts::FreeSansBold9pt7b);
  else
    frame.setFont(&fonts::Font2);
  frame.setTextColor(MUTED, BG);
  frame.drawString(countdown, center, top + 29);
}

void drawScreen() {
  frame.fillScreen(BG);
  frame.setFont(&fonts::Font0);
  frame.setTextDatum(top_left);
  bool stale = false;
  for (size_t i = 0; i < snapshot.count; ++i)
    stale |= isStale(snapshot.accounts[i]);
  const char *state = !settings.ready() ? "setup" : offline() ? "offline" : stale ? "stale" : "";
  frame.setTextColor((offline() || stale) ? AMBER : MUTED, BG);
  frame.drawString(state, 4, 4);
  frame.setTextDatum(top_center);
  frame.setTextColor(MUTED, BG);
  frame.drawString("5h", SHORT_COLUMN_X, 4);
  frame.drawString("week", WEEK_COLUMN_X, 4);
  for (size_t i = 0; i < snapshot.count; ++i) {
    int top = 17 + i * 39;
    frame.setFont(&fonts::Font2);
    frame.setTextDatum(middle_left);
    frame.setTextColor(TEXT, BG);
    frame.drawString(snapshot.accounts[i].label, 4, top + 10);
    frame.setFont(&fonts::Font0);
    if (isStale(snapshot.accounts[i]) && gotSnapshot) {
      frame.setTextColor(AMBER, BG);
      frame.drawString("*", 28, top + 9);
    }
    frame.fillCircle(36, top + 10, 1, MUTED);
    frame.setTextColor(MUTED, BG);
    frame.drawString("used", 44, top + 10);
    frame.drawString("reset", 44, top + 29);
    drawWindow(snapshot.accounts[i].shortWindow, SHORT_COLUMN_X, top);
    drawWindow(snapshot.accounts[i].week, WEEK_COLUMN_X, top);
  }
  frame.pushSprite(0, 0);
  lastDraw = millis();
}

void loadSettings() {
  preferences.begin("m5usage", false);
  String saved = preferences.getString("config", "");
  if (saved.isEmpty())
    return;
  JsonDocument doc;
  if (deserializeJson(doc, saved))
    return;
  settings.ssid = doc["ssid"].as<String>();
  settings.password = doc["password"].as<String>();
  settings.host = doc["host"].as<String>();
  settings.token = doc["token"].as<String>();
  settings.port = doc["port"] | 8765;
}

void joinWiFi() {
  if (!settings.ready())
    return;
  WiFi.disconnect();
  WiFi.begin(settings.ssid.c_str(), settings.password.c_str());
  lastJoin = millis();
  lastRequestOK = false;
  serverIP = IPAddress();
  logEvent("wifi_connecting");
}

bool applySnapshot(const String &payload) {
  JsonDocument doc;
  if (deserializeJson(doc, payload))
    return false;
  if (!readSnapshot(doc, snapshot))
    return false;
  M5.Display.setBrightness(snapshot.display.brightness);
  // Account for time spent inside DNS / HTTP since the beginning of this loop.
  clockMillis += uint32_t(millis() - previousMillis);
  previousMillis = millis();
  timeAnchorMillis = clockMillis;
  gotSnapshot = true;
  return true;
}

void fetchSnapshot() {
  nextPoll = millis() + snapshot.display.pollSeconds * 1000;
  if (WiFi.status() != WL_CONNECTED)
    return;
  if (!mdnsStarted)
    mdnsStarted = MDNS.begin("m5-ai-usage");
  if (serverIP == IPAddress()) {
    // A literal address is useful for diagnostics; normal setup uses Bonjour.
    if (!serverIP.fromString(settings.host)) {
      String host = settings.host;
      if (host.endsWith(".local"))
        host.remove(host.length() - 6);
      serverIP = MDNS.queryHost(host, 2000);
    }
  }
  if (serverIP == IPAddress()) {
    lastError = "dns";
    lastRequestOK = false;
    logEvent("host_unavailable");
    return;
  }
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(2000);
  http.setTimeout(3000);
  String url = "http://" + serverIP.toString() + ":" + settings.port + "/v1/status";
  if (!http.begin(client, url)) {
    lastRequestOK = false;
    lastError = "http_init";
    return;
  }
  http.addHeader("Authorization", "Bearer " + settings.token);
  int code = http.GET();
  bool ok = false;
  if (code == 200 && http.getSize() > 0 && http.getSize() <= 8192) {
    ok = applySnapshot(http.getString());
  }
  http.end();
  lastRequestOK = ok;
  if (ok) {
    lastSuccess = millis();
    lastError = "";
    logEvent("snapshot_received");
  } else {
    lastError = code == 401   ? "unauthorized"
                : code == 200 ? "invalid_snapshot"
                              : "bridge_unavailable";
    serverIP = IPAddress();
    Serial.printf("{\"event\":\"fetch_failed\",\"code\":%d}\n", code);
  }
  nextPoll = millis() + snapshot.display.pollSeconds * 1000;
  drawScreen();
}

void writeStatus() {
  JsonDocument doc;
  doc["event"] = "status";
  doc["version"] = VERSION;
  doc["configured"] = settings.ready();
  doc["wifi"] = WiFi.status() == WL_CONNECTED;
  doc["ip"] = WiFi.localIP().toString();
  doc["host"] = settings.host;
  doc["port"] = settings.port;
  doc["bridge_ip"] = serverIP.toString();
  doc["offline"] = offline();
  doc["error"] = lastError;
  doc["uptime_ms"] = millis();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["rssi"] = WiFi.RSSI();
  doc["width"] = frame.width();
  doc["height"] = frame.height();
  doc["now"] = nowSeconds();
  auto rows = doc["accounts"].to<JsonArray>();
  for (size_t i = 0; i < snapshot.count; ++i) {
    auto row = rows.add<JsonObject>();
    row["id"] = snapshot.accounts[i].id;
    row["label"] = snapshot.accounts[i].label;
    row["stale"] = isStale(snapshot.accounts[i]);
    row["fetched_at"] = snapshot.accounts[i].fetched;
    if (snapshot.accounts[i].shortWindow.present)
      row["short_used"] = snapshot.accounts[i].shortWindow.used;
    if (snapshot.accounts[i].week.present)
      row["week_used"] = snapshot.accounts[i].week.used;
  }
  serializeJson(doc, Serial);
  Serial.println();
}

void writeScreenshot() {
  drawScreen();
  Serial.printf("FRAME %d %d RGB888\n", frame.width(), frame.height());
  uint8_t row[240 * 3];
  for (int y = 0; y < frame.height(); ++y) {
    frame.readRectRGB(0, y, frame.width(), 1, row);
    Serial.write(row, sizeof(row));
  }
  Serial.println("\nEND_FRAME");
  Serial.flush();
}

void serialCommand(const String &line) {
  JsonDocument doc;
  if (deserializeJson(doc, line)) {
    logEvent("invalid_command");
    return;
  }
  String command = doc["command"] | "";
  if (command == "status")
    writeStatus();
  else if (command == "screenshot")
    writeScreenshot();
  else if (command == "reboot") {
    logEvent("rebooting");
    Serial.flush();
    ESP.restart();
  } else if (command == "reconnect") {
    joinWiFi();
    nextPoll = millis();
  } else if (command == "configure") {
    if (!doc["ssid"].is<String>() || !doc["password"].is<String>() || !doc["host"].is<String>() ||
        !doc["token"].is<String>() || !doc["port"].is<int>()) {
      logEvent("invalid_config");
      return;
    }
    Settings next;
    next.ssid = doc["ssid"].as<String>();
    next.password = doc["password"].as<String>();
    next.host = doc["host"].as<String>();
    next.token = doc["token"].as<String>();
    int port = doc["port"] | 8765;
    if (!next.ready() || next.ssid.length() > 32 || next.password.length() > 63 ||
        next.host.length() > 253 || next.token.length() > 128 || port < 1 || port > 65535) {
      logEvent("invalid_config");
      return;
    }
    next.port = port;
    // Serialize the complete configuration as one NVS item so interrupted
    // writes cannot combine credentials from different configurations.
    JsonDocument stored;
    stored["ssid"] = next.ssid;
    stored["password"] = next.password;
    stored["host"] = next.host;
    stored["token"] = next.token;
    stored["port"] = next.port;
    String encoded;
    serializeJson(stored, encoded);
    if (!preferences.putString("config", encoded)) {
      logEvent("save_failed");
      return;
    }
    settings = next;
    logEvent("configured");
    joinWiFi();
    nextPoll = millis();
    drawScreen();
  } else
    logEvent("unknown_command");
}

void readSerial() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n') {
      serialCommand(serialLine);
      serialLine = "";
    } else if (ch != '\r') {
      if (serialLine.length() >= 2048)
        serialLine = "";
      else
        serialLine += ch;
    }
  }
}
} // namespace

void setup() {
  // Keep Plus2 powered from its battery as soon as firmware starts.
  pinMode(4, OUTPUT);
  digitalWrite(4, HIGH);
  auto cfg = M5.config();
  cfg.serial_baudrate = 115200;
  cfg.internal_imu = false;
  cfg.internal_rtc = false;
  cfg.internal_mic = false;
  cfg.internal_spk = false;
  cfg.external_display_value = 0;
  cfg.fallback_board = m5::board_t::board_M5StickCPlus2;
  M5.begin(cfg);
  M5.Display.setRotation(1);
  M5.Display.setBrightness(snapshot.display.brightness);
  frame.setColorDepth(16);
  if (!frame.createSprite(240, 135)) {
    Serial.println("{\"event\":\"framebuffer_failed\"}");
    while (true)
      delay(1000);
  }
  loadSettings();
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("m5-ai-usage");
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);
  drawScreen();
  joinWiFi();
  previousMillis = millis();
  logEvent("ready");
}

void loop() {
  uint32_t current = millis();
  clockMillis += uint32_t(current - previousMillis);
  previousMillis = current;
  M5.update();
  readSerial();
  if (settings.ready()) {
    if (WiFi.status() != WL_CONNECTED && uint32_t(millis() - lastJoin) >= RETRY_MS)
      joinWiFi();
    if (WiFi.status() == WL_CONNECTED && int32_t(millis() - nextPoll) >= 0)
      fetchSnapshot();
    if (M5.BtnA.wasClicked())
      nextPoll = millis();
  }
  if (uint32_t(millis() - lastDraw) >= 1000)
    drawScreen();
  delay(10);
}
