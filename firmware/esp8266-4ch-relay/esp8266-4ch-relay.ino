#include <ArduinoJson.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>

#include "secrets.h"

const uint8_t RELAY_COUNT = 4;
const uint8_t RELAY_PINS[RELAY_COUNT] = {D0, D1, D2, D5};
const bool RELAY_ACTIVE_LOW = true;
const unsigned long MAX_PULSE_DURATION_MS = 300000;

const uint8_t RELAY_ON_LEVEL = RELAY_ACTIVE_LOW ? LOW : HIGH;

ESP8266WebServer server(80);
bool relayIsOn[RELAY_COUNT] = {
    RELAY_ACTIVE_LOW, RELAY_ACTIVE_LOW, RELAY_ACTIVE_LOW, RELAY_ACTIVE_LOW};
bool pulseIsActive[RELAY_COUNT] = {false, false, false, false};
unsigned long pulseEndsAt[RELAY_COUNT] = {0, 0, 0, 0};

void setRelay(uint8_t channel, bool on) {
  relayIsOn[channel] = on;
  digitalWrite(RELAY_PINS[channel], on ? RELAY_ON_LEVEL : !RELAY_ON_LEVEL);
}

void sendJsonError(int statusCode, const char* message) {
  StaticJsonDocument<128> response;
  response["error"] = message;
  String body;
  serializeJson(response, body);
  server.send(statusCode, "application/json", body);
}

bool getChannelIndex(uint8_t channelNumber, uint8_t& channelIndex) {
  if (channelNumber == 0 || channelNumber > RELAY_COUNT) {
    sendJsonError(404, "Relay channel must be between 1 and 4.");
    return false;
  }
  channelIndex = channelNumber - 1;
  return true;
}

void expireRelayPulse(uint8_t channelIndex) {
  if (pulseIsActive[channelIndex] &&
      static_cast<long>(millis() - pulseEndsAt[channelIndex]) >= 0) {
    pulseIsActive[channelIndex] = false;
    setRelay(channelIndex, false);
  }
}

void handleRelayStatus(uint8_t channelNumber) {
  uint8_t channelIndex;
  if (!getChannelIndex(channelNumber, channelIndex)) {
    return;
  }

  expireRelayPulse(channelIndex);
  StaticJsonDocument<192> response;
  response["channel"] = channelNumber;
  response["state"] = relayIsOn[channelIndex] ? "on" : "off";
  response["mode"] = pulseIsActive[channelIndex] ? "pulse" : "latching";
  response["remaining_ms"] =
      pulseIsActive[channelIndex] && relayIsOn[channelIndex]
          ? static_cast<unsigned long>(pulseEndsAt[channelIndex] - millis())
          : 0;

  String body;
  serializeJson(response, body);
  server.send(200, "application/json", body);
}

void handleRelayCommand(uint8_t channelNumber) {
  uint8_t channelIndex;
  if (!getChannelIndex(channelNumber, channelIndex)) {
    return;
  }
  if (server.arg("plain").length() == 0) {
    sendJsonError(400, "Request body must be a JSON object.");
    return;
  }

  StaticJsonDocument<256> request;
  DeserializationError parseError = deserializeJson(request, server.arg("plain"));
  if (parseError || !request.is<JsonObject>()) {
    sendJsonError(400, "Request body must be a valid JSON object.");
    return;
  }

  const char* mode = request["mode"];
  if (mode == nullptr) {
    sendJsonError(400, "Mode must be 'latching' or 'pulse'.");
    return;
  }

  if (strcmp(mode, "latching") == 0) {
    const char* state = request["state"];
    if (state == nullptr ||
        (strcmp(state, "on") != 0 && strcmp(state, "off") != 0)) {
      sendJsonError(400, "Latching mode requires state 'on' or 'off'.");
      return;
    }

    pulseIsActive[channelIndex] = false;
    setRelay(channelIndex, strcmp(state, "on") == 0);
    handleRelayStatus(channelNumber);
    return;
  }

  if (strcmp(mode, "pulse") == 0) {
    if (!request["duration_ms"].is<unsigned long>()) {
      sendJsonError(400, "Pulse mode requires an integer duration_ms.");
      return;
    }
    const unsigned long durationMs = request["duration_ms"].as<unsigned long>();
    if (durationMs == 0 || durationMs > MAX_PULSE_DURATION_MS) {
      sendJsonError(400, "duration_ms must be between 1 and 300000.");
      return;
    }

    pulseIsActive[channelIndex] = true;
    pulseEndsAt[channelIndex] = millis() + durationMs;
    setRelay(channelIndex, true);
    handleRelayStatus(channelNumber);
    return;
  }

  sendJsonError(400, "Mode must be 'latching' or 'pulse'.");
}

void handleNotFound() {
  sendJsonError(404, "Not found.");
}

void connectToWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("Connected. Relay IP address: http://");
  Serial.println(WiFi.localIP());
}

void setup() {
  for (uint8_t channel = 0; channel < RELAY_COUNT; ++channel) {
    digitalWrite(RELAY_PINS[channel], LOW);
    pinMode(RELAY_PINS[channel], OUTPUT);
    relayIsOn[channel] = RELAY_ACTIVE_LOW;
  }

  Serial.begin(115200);
  connectToWiFi();

  server.on("/api/relay/1", HTTP_GET, []() { handleRelayStatus(1); });
  server.on("/api/relay/1", HTTP_POST, []() { handleRelayCommand(1); });
  server.on("/api/relay/2", HTTP_GET, []() { handleRelayStatus(2); });
  server.on("/api/relay/2", HTTP_POST, []() { handleRelayCommand(2); });
  server.on("/api/relay/3", HTTP_GET, []() { handleRelayStatus(3); });
  server.on("/api/relay/3", HTTP_POST, []() { handleRelayCommand(3); });
  server.on("/api/relay/4", HTTP_GET, []() { handleRelayStatus(4); });
  server.on("/api/relay/4", HTTP_POST, []() { handleRelayCommand(4); });
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("Four-channel relay API ready at /api/relay/1 through /api/relay/4");
}

void loop() {
  server.handleClient();
  for (uint8_t channel = 0; channel < RELAY_COUNT; ++channel) {
    expireRelayPulse(channel);
  }
}
