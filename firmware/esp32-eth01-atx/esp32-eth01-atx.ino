#include <ETH.h>
#include <WebServer.h>

const uint8_t POWER_BUTTON_PIN = 32;
const uint8_t RESET_BUTTON_PIN = 33;
const uint8_t POWER_LED_PIN = 13;
const uint8_t HDD_LED_PIN = 14;

const int PHY_ADDRESS = 1;
const int PHY_MDC_PIN = 23;
const int PHY_MDIO_PIN = 18;
const int PHY_POWER_PIN = -1;
const eth_phy_type_t PHY_TYPE = ETH_PHY_LAN8720;
const eth_clock_mode_t PHY_CLOCK_MODE = ETH_CLOCK_GPIO0_IN;

const unsigned long BUTTON_PULSE_MS = 500;
const unsigned long POWER_LED_DEBOUNCE_MS = 15;
const unsigned long HDD_LED_DEBOUNCE_MS = 3;

WebServer server(80);

struct ButtonOutput {
  uint8_t pin;
  bool active;
  unsigned long releaseAt;
};

struct LedInput {
  uint8_t pin;
  unsigned long debounceMs;
  bool rawOn;
  bool isOn;
  unsigned long rawChangedAt;
};

ButtonOutput powerButton = {POWER_BUTTON_PIN, false, 0};
ButtonOutput resetButton = {RESET_BUTTON_PIN, false, 0};
LedInput powerLed = {POWER_LED_PIN, POWER_LED_DEBOUNCE_MS, false, false, 0};
LedInput hddLed = {HDD_LED_PIN, HDD_LED_DEBOUNCE_MS, false, false, 0};

uint32_t hddActivityCount = 0;
IPAddress lastReportedIp(0, 0, 0, 0);

void beginButton(ButtonOutput& button) {
  digitalWrite(button.pin, LOW);
  pinMode(button.pin, OUTPUT);
}

void beginLedInput(LedInput& input) {
  pinMode(input.pin, INPUT_PULLUP);
  input.rawOn = digitalRead(input.pin) == LOW;
  input.isOn = input.rawOn;
  input.rawChangedAt = millis();
}

bool updateLedInput(LedInput& input) {
  const unsigned long now = millis();
  const bool reading = digitalRead(input.pin) == LOW;

  if (reading != input.rawOn) {
    input.rawOn = reading;
    input.rawChangedAt = now;
  }

  if (input.isOn != input.rawOn &&
      now - input.rawChangedAt >= input.debounceMs) {
    input.isOn = input.rawOn;
    return true;
  }

  return false;
}

void serviceLedInputs() {
  updateLedInput(powerLed);
  if (updateLedInput(hddLed) && hddLed.isOn) {
    ++hddActivityCount;
  }
}

void serviceButton(ButtonOutput& button) {
  if (button.active &&
      static_cast<int32_t>(millis() - button.releaseAt) >= 0) {
    digitalWrite(button.pin, LOW);
    button.active = false;
  }
}

bool pulseButton(ButtonOutput& button) {
  if (button.active) {
    return false;
  }

  digitalWrite(button.pin, HIGH);
  button.active = true;
  button.releaseAt = millis() + BUTTON_PULSE_MS;
  return true;
}

void sendJsonError(int statusCode, const char* message) {
  String body = "{\"error\":\"";
  body += message;
  body += "\"}";
  server.send(statusCode, "application/json", body);
}

void handleStatus() {
  serviceLedInputs();

  String body = "{\"power_led\":";
  body += powerLed.isOn ? "true" : "false";
  body += ",\"hdd_led\":";
  body += hddLed.isOn ? "true" : "false";
  body += ",\"hdd_activity_count\":";
  body += String(hddActivityCount);
  body += ",\"power_button_active\":";
  body += powerButton.active ? "true" : "false";
  body += ",\"reset_button_active\":";
  body += resetButton.active ? "true" : "false";
  body += "}";
  server.send(200, "application/json", body);
}

void handlePowerCommand() {
  if (!pulseButton(powerButton)) {
    sendJsonError(409, "Power button pulse is already active.");
    return;
  }

  server.send(202, "application/json",
              "{\"command\":\"power\",\"pulse_ms\":500}");
}

void handleResetCommand() {
  if (!pulseButton(resetButton)) {
    sendJsonError(409, "Reset button pulse is already active.");
    return;
  }

  server.send(202, "application/json",
              "{\"command\":\"reset\",\"pulse_ms\":500}");
}

void handleNotFound() {
  sendJsonError(404, "Not found.");
}

void reportEthernetAddress() {
  const IPAddress currentIp = ETH.localIP();
  if (currentIp != lastReportedIp) {
    lastReportedIp = currentIp;
    if (currentIp != IPAddress(0, 0, 0, 0)) {
      Serial.print("ATX controller IP address: http://");
      Serial.println(currentIp);
    } else {
      Serial.println("Ethernet has no IP address.");
    }
  }
}

void setup() {
  Serial.begin(115200);

  beginButton(powerButton);
  beginButton(resetButton);
  beginLedInput(powerLed);
  beginLedInput(hddLed);

  if (!ETH.begin(PHY_TYPE, PHY_ADDRESS, PHY_MDC_PIN, PHY_MDIO_PIN,
                 PHY_POWER_PIN, PHY_CLOCK_MODE)) {
    Serial.println("Ethernet initialization failed.");
  }

  server.on("/api/pc/status", HTTP_GET, handleStatus);
  server.on("/api/pc/power", HTTP_POST, handlePowerCommand);
  server.on("/api/pc/reset", HTTP_POST, handleResetCommand);
  server.onNotFound(handleNotFound);
  server.begin();

  Serial.println("ATX controller API ready.");
}

void loop() {
  server.handleClient();
  serviceButton(powerButton);
  serviceButton(resetButton);
  serviceLedInputs();
  reportEthernetAddress();
}
