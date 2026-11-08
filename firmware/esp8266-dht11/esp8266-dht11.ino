#include <DHT.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>

#include "secrets.h"

#define DHT_PIN D6
#define DHT_TYPE DHT11

const unsigned long SENSOR_READ_INTERVAL_MS = 2500;

DHT dht(DHT_PIN, DHT_TYPE);
ESP8266WebServer server(80);
float temperatureC = NAN;
float humidity = NAN;
unsigned long lastSensorRead = 0;

void handleTemperature() {
  if (isnan(temperatureC) || isnan(humidity)) {
    server.send(503, "application/json", "{\"error\":\"DHT11 reading unavailable\"}");
    return;
  }

  String response = "{\"temperature\":";
  response += String(temperatureC, 1);
  response += ",\"humidity\":";
  response += String(humidity, 1);
  response += "}";
  server.send(200, "application/json", response);
}

void handleNotFound() {
  server.send(404, "application/json", "{\"error\":\"Not found\"}");
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
  Serial.print("Connected. Sensor IP address: http://");
  Serial.println(WiFi.localIP());
}

void setup() {
  Serial.begin(115200);
  dht.begin();
  connectToWiFi();

  server.on("/api/temperature", HTTP_GET, handleTemperature);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("Temperature API ready at /api/temperature");
}

void loop() {
  server.handleClient();

  const unsigned long now = millis();
  if (now - lastSensorRead >= SENSOR_READ_INTERVAL_MS) {
    lastSensorRead = now;
    temperatureC = dht.readTemperature();
    humidity = dht.readHumidity();

    if (isnan(temperatureC) || isnan(humidity)) {
      Serial.println("DHT11 read failed; will retry.");
    } else {
      Serial.print("Temperature: ");
      Serial.print(temperatureC, 1);
      Serial.print(" C, humidity: ");
      Serial.print(humidity, 1);
      Serial.println(" %");
    }
  }
}
