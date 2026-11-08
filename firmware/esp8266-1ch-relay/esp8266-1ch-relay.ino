#include <ArduinoJson.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>

#include "secrets.h"

const uint8_t RELAY_PIN = D0;
const bool RELAY_ACTIVE_LOW = true;
const unsigned long MAX_PULSE_DURATION_MS = 300000;

const uint8_t RELAY_ON_LEVEL = RELAY_ACTIVE_LOW ? LOW : HIGH;
const uint8_t RELAY_OFF_LEVEL = RELAY_ACTIVE_LOW ? HIGH : LOW;

ESP8266WebServer server(80);
bool relayIsOn = false;
bool pulseIsActive = false;
unsigned long pulseEndsAt = 0;

void setRelay(bool on) {
  relayIsOn = on;
  digitalWrite(RELAY_PIN, on ? RELAY_ON_LEVEL : RELAY_OFF_LEVEL);
}

void sendJsonError(int statusCode, const char* message) {
  StaticJsonDocument<128> response;
  response["error"] = message;
  String body;
  serializeJson(response, body);
  server.send(statusCode, "application/json", body);
}

void expireRelayPulse() {
  if (pulseIsActive &&
      static_cast<long>(millis() - pulseEndsAt) >= 0) {
    pulseIsActive = false;
    setRelay(false);
  }
}

void handleRelayStatus() {
  expireRelayPulse();
  StaticJsonDocument<192> response;
  response["state"] = relayIsOn ? "on" : "off";
  response["mode"] = pulseIsActive ? "pulse" : "latching";
  if (pulseIsActive && relayIsOn) {
    response["remaining_ms"] =
        static_cast<unsigned long>(pulseEndsAt - millis());
  } else {
    response["remaining_ms"] = 0;
  }

  String body;
  serializeJson(response, body);
  server.send(200, "application/json", body);
}

void handleRelayCommand() {
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

    pulseIsActive = false;
    setRelay(strcmp(state, "on") == 0);
    handleRelayStatus();
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

    pulseIsActive = true;
    pulseEndsAt = millis() + durationMs;
    setRelay(true);
    handleRelayStatus();
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
  digitalWrite(RELAY_PIN, LOW);
  pinMode(RELAY_PIN, OUTPUT);
  relayIsOn = RELAY_ACTIVE_LOW;

  Serial.begin(115200);
  connectToWiFi();

  server.on("/api/relay", HTTP_GET, handleRelayStatus);
  server.on("/api/relay", HTTP_POST, handleRelayCommand);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("Relay API ready at /api/relay");
}

void loop() {
  server.handleClient();
  expireRelayPulse();
}
