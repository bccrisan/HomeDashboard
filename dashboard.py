import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import json
import math
import os
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.parse import urlsplit


CONFIG_PATH = Path(__file__).with_name("wifi-sensors.json")
DEFAULT_DEVICE_TYPE = "temperature_humidity"
RELAY_ROUTE_PREFIX = "/api/relay/"
DEVICE_TYPES = {
    "temperature_humidity": {
        "label": "Temperature and humidity sensor",
        "mode": "temperature_humidity",
    },
    "relay": {
        "label": "1 Channel relay",
        "mode": "relay",
    },
    "relay_2ch": {
        "label": "2 Channel relay",
        "mode": "relay_channels",
        "channel_count": 2,
        "channel_pins": ["D0", "D1"],
    },
    "relay_4ch": {
        "label": "4 Channel relay",
        "mode": "relay_channels",
        "channel_count": 4,
        "channel_pins": ["D0", "D1", "D2", "D5"],
    },
    "relay_8ch": {
        "label": "8 Channel relay (74HC595N)",
        "mode": "relay_channels",
        "channel_count": 8,
        "channel_pins": ["Q0", "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"],
    },
}


class RelayCommunicationError(Exception):
    pass


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Home Dashboard</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; color: #172033; background: #f3f6fb; }
    body { margin: 0; }
    .dashboard-layout { display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: 100vh; }
    .server-panel { padding: 32px 20px; background: white; border-right: 1px solid #e1e7f0; }
    .server-panel h2 { margin: 0 0 18px; font-size: 1.2rem; }
    .server-list { display: grid; gap: 10px; }
    .server-item { display: block; padding: 12px; border: 1px solid #e1e7f0; border-radius: 10px; color: inherit; text-decoration: none; }
    .server-item:hover { border-color: #9aacc8; box-shadow: 0 3px 12px #17203312; }
    .server-item:focus-visible { outline: 3px solid #2457c5; outline-offset: 2px; }
    .server-name { margin: 0 0 5px; font-weight: 650; }
    .server-address { color: #647086; font-size: .85rem; }
    .server-status { display: flex; align-items: center; gap: 7px; margin-top: 8px; font-size: .9rem; font-weight: 600; }
    .server-status::before { width: 9px; height: 9px; border-radius: 50%; background: #a4262c; content: ""; }
    .server-status.available::before { background: #27864a; }
    .server-empty { color: #647086; font-size: .9rem; }
    main { max-width: 960px; width: 100%; box-sizing: border-box; margin: 0 auto; padding: 48px 24px; }
    h1 { margin: 0; font-size: clamp(2rem, 5vw, 3rem); }
    .intro { color: #596579; margin: 8px 0 28px; }
    .notice { padding: 12px 16px; border-radius: 10px; background: #e7efff; color: #284b83; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 16px; margin-top: 24px; }
    .card { background: white; border: 1px solid #e1e7f0; border-radius: 14px; padding: 20px; box-shadow: 0 4px 16px #1720330b; }
    .room { color: #647086; margin: 4px 0 20px; }
    .device-type { color: #2457c5; font-size: .85rem; font-weight: 600; margin: -14px 0 16px; }
    .readings { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
    .reading { font-size: 2.5rem; font-weight: 700; letter-spacing: -0.06em; }
    .relay-button { padding: 10px 18px; border: 0; border-radius: 8px; background: #2457c5; color: white; font: inherit; font-weight: 700; cursor: pointer; }
    .relay-button:hover { background: #1c469f; }
    .relay-button.on { background: #c62828; }
    .relay-button.on:hover { background: #a51f1f; }
    .relay-button:disabled { cursor: wait; opacity: .65; }
    .last-pressed { color: #647086; font-size: .9rem; }
    .ip { color: #647086; font-size: .9rem; margin-top: 14px; }
    .status { color: #647086; margin-top: 24px; font-size: .9rem; }
    .error { color: #a4262c; }
    .settings-link { position: fixed; right: 24px; bottom: 24px; padding: 12px 18px; border-radius: 999px; background: #2457c5; color: white; text-decoration: none; font-weight: 600; box-shadow: 0 4px 14px #17203333; }
    .settings-link:hover { background: #1c469f; }
    @media (max-width: 760px) {
      .dashboard-layout { grid-template-columns: 1fr; }
      .server-panel { border-right: 0; border-bottom: 1px solid #e1e7f0; padding: 20px 24px; }
      .server-list { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
      main { padding: 32px 24px; }
    }
  </style>
</head>
<body>
  <div class="dashboard-layout">
    <aside class="server-panel" aria-labelledby="server-heading">
      <h2 id="server-heading">Local servers</h2>
      <section id="servers" class="server-list" aria-live="polite"></section>
    </aside>
    <main>
      <h1>Home Dashboard</h1>
      <p class="intro">Live status for your configured sensors and modules.</p>
      <p class="notice">Live readings are requested from each sensor on your local network.</p>
      <section id="sensors" class="grid" aria-live="polite"></section>
      <p id="status" class="status">Loading sensors…</p>
    </main>
  </div>
  <a class="settings-link" href="/settings">Configuration</a>
  <script>
    const sensorGrid = document.querySelector("#sensors");
    const serverList = document.querySelector("#servers");
    const status = document.querySelector("#status");
    let refreshTimer;

    async function refreshSensors() {
      window.clearTimeout(refreshTimer);
      try {
        const [sensorResponse, serverResponse] = await Promise.all([
          fetch("/api/sensors", { cache: "no-store" }),
          fetch("/api/servers", { cache: "no-store" })
        ]);
        const [data, serverData] = await Promise.all([
          sensorResponse.json(),
          serverResponse.json()
        ]);
        if (!sensorResponse.ok) throw new Error(data.error || "Unable to load sensors.");
        if (!serverResponse.ok) throw new Error(serverData.error || "Unable to load monitored servers.");

        serverList.replaceChildren();
        if (serverData.servers.length === 0) {
          const empty = document.createElement("p");
          empty.className = "server-empty";
          empty.textContent = "No monitored servers configured.";
          serverList.append(empty);
        }
        for (const server of serverData.servers) {
          const item = document.createElement("a");
          item.className = "server-item";
          item.href = server.address.toLowerCase().startsWith("http://")
            || server.address.toLowerCase().startsWith("https://")
            ? server.address
            : `http://${server.address}`;
          item.target = "_blank";
          item.rel = "noopener noreferrer";
          const name = document.createElement("p");
          name.className = "server-name";
          name.textContent = server.name;
          const address = document.createElement("div");
          address.className = "server-address";
          address.textContent = server.address;
          const availability = document.createElement("div");
          availability.className = `server-status${server.status === "available" ? " available" : ""}`;
          availability.textContent = server.status === "available" ? "Available" : "Offline";
          if (server.error) availability.title = server.error;
          item.append(name, address, availability);
          serverList.append(item);
        }

        sensorGrid.replaceChildren();
        for (const sensor of data.sensors) {
          const card = document.createElement("article");
          card.className = "card";

          const name = document.createElement("h2");
          name.textContent = sensor.name;
          const room = document.createElement("p");
          room.className = "room";
          room.textContent = sensor.room_name;
          const deviceType = document.createElement("p");
          deviceType.className = "device-type";
          deviceType.textContent = sensor.device_type_label;
          const readings = document.createElement("div");
          readings.className = "readings";
          if (sensor.device_type === "temperature_humidity") {
            const temperature = document.createElement("div");
            temperature.className = "reading";
            temperature.textContent = sensor.status === "online"
              ? `${sensor.temperature} °C`
              : "Offline";
            const humidity = document.createElement("div");
            humidity.className = "reading";
            humidity.textContent = sensor.status === "online"
              ? `${sensor.humidity}%`
              : "—%";
            readings.append(temperature, humidity);
          }
          const ip = document.createElement("p");
          ip.className = "ip";
          ip.textContent = `Local IP: ${sensor.local_ip_address}`;
          if (sensor.error) {
            ip.title = sensor.error;
          }

          card.append(name, room, deviceType, readings);
          if (sensor.device_type === "relay" || Array.isArray(sensor.channels)) {
            const isMultiChannel = Array.isArray(sensor.channels);
            const channels = isMultiChannel
              ? sensor.channels
              : [{ ...sensor, channel: 1 }];
            for (const channel of channels) {
              const channelNumber = channel.channel;
              const relayIsOn = channel.relay_state === "on";
              const relayButton = document.createElement("button");
              relayButton.type = "button";
              relayButton.className = "relay-button";
              relayButton.disabled = channel.status !== "online";
              relayButton.classList.toggle("on", relayIsOn);
              relayButton.textContent = channel.status !== "online"
                ? `Channel ${channelNumber} offline`
                : channel.relay_mode === "pulse"
                  ? (relayIsOn ? `Channel ${channelNumber}: Turn off` : `Channel ${channelNumber}: Turn on (pulse)`)
                  : (relayIsOn ? `Channel ${channelNumber}: Turn off` : `Channel ${channelNumber}: Turn on`);
              relayButton.addEventListener("click", async () => {
                relayButton.disabled = true;
                try {
                  const relayPath = isMultiChannel
                    ? `/api/relay/${sensor.index}/${channelNumber}`
                    : `/api/relay/${sensor.index}`;
                  const response = await fetch(relayPath, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: "activate" })
                  });
                  const result = await response.json();
                  if (!response.ok) throw new Error(result.error || "Relay command failed.");
                  await refreshSensors();
                } catch (error) {
                  status.className = "status error";
                  status.textContent = `Could not control ${sensor.name}, channel ${channelNumber}: ${error.message}`;
                  relayButton.disabled = false;
                }
              });
              const lastPressed = document.createElement("p");
              lastPressed.className = "last-pressed";
              lastPressed.textContent = channel.last_pressed_at
                ? `Channel ${channelNumber} last pressed: ${new Date(channel.last_pressed_at).toLocaleString()}`
                : "Last pressed: never";
              card.append(relayButton, lastPressed);
            }
          }
          card.append(ip);
          sensorGrid.append(card);
        }
        status.className = "status";
        const offlineCount = data.sensors.filter((sensor) => sensor.status === "offline").length;
        status.textContent = `Updated ${new Date().toLocaleTimeString()}. Refreshes every 30 seconds.${offlineCount ? ` ${offlineCount} sensor(s) offline.` : ""}`;
        const pulseActive = data.sensors.some((sensor) => {
          const channels = sensor.device_type !== "relay" && sensor.channels
            ? sensor.channels
            : sensor.device_type === "relay"
              ? [sensor]
              : [];
          return channels.some((channel) => channel.relay_mode === "pulse"
            && channel.status === "online"
            && channel.relay_state === "on");
        });
        refreshTimer = window.setTimeout(refreshSensors, pulseActive ? 500 : 30000);
      } catch (error) {
        status.className = "status error";
        status.textContent = `Could not load sensors: ${error.message}`;
        refreshTimer = window.setTimeout(refreshSensors, 30000);
      }
    }

    refreshSensors();
  </script>
</body>
</html>
"""

SETTINGS_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Device Configuration</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; color: #172033; background: #f3f6fb; }
    body { margin: 0; }
    main { max-width: 760px; margin: 0 auto; padding: 40px 24px; }
    h1 { margin: 0; font-size: clamp(2rem, 5vw, 3rem); }
    .intro, .back { color: #596579; }
    .back { display: inline-block; margin-bottom: 20px; text-decoration: none; }
    .sensor-list { display: grid; gap: 16px; margin: 24px 0; }
    .sensor { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; padding: 20px; background: white; border: 1px solid #e1e7f0; border-radius: 14px; }
    .section-heading { margin: 32px 0 8px; }
    .section-intro { margin: 0; color: #596579; }
    .monitored-device-list { display: grid; gap: 16px; margin: 18px 0 24px; }
    .monitored-device { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; padding: 20px; background: white; border: 1px solid #e1e7f0; border-radius: 14px; }
    label { display: grid; gap: 6px; color: #596579; font-size: .9rem; }
    input, select { box-sizing: border-box; width: 100%; padding: 10px 12px; border: 1px solid #cbd4e1; border-radius: 8px; color: #172033; background: white; font: inherit; }
    .relay-options { display: contents; }
    .relay-options[hidden] { display: none; }
    .relay-state-field[hidden], .pulse-duration-field[hidden] { display: none; }
    .multi-relay-options { grid-column: 1 / -1; }
    .multi-relay-options[hidden] { display: none; }
    .channel-configs { display: grid; gap: 12px; }
    .channel-config { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; min-width: 0; border: 1px solid #e1e7f0; border-radius: 10px; }
    .channel-config legend { padding: 0 6px; color: #2457c5; font-weight: 600; }
    .channel-config [hidden] { display: none; }
    button { padding: 10px 14px; border: 0; border-radius: 8px; background: #2457c5; color: white; font: inherit; font-weight: 600; cursor: pointer; }
    button:hover { background: #1c469f; }
    .remove { align-self: end; background: #a4262c; }
    .remove:hover { background: #851e23; }
    .actions { display: flex; gap: 12px; flex-wrap: wrap; }
    .status { min-height: 1.5em; margin-top: 16px; color: #276749; }
    .error { color: #a4262c; }
  </style>
</head>
<body>
  <main>
    <a class="back" href="/">&larr; Back to dashboard</a>
    <h1>Device configuration</h1>
    <p class="intro">Configure sensors and relay modules. Each relay channel can have its own mode, state, and pulse duration.</p>
    <form id="config-form">
      <h2 class="section-heading">Sensors and relay modules</h2>
      <section id="sensor-list" class="sensor-list"></section>
      <h2 class="section-heading">Monitored devices</h2>
      <p class="section-intro">Track whether local servers respond to ping or their configured HTTP URL. These devices are separate from sensors and relays.</p>
      <section id="monitored-device-list" class="monitored-device-list"></section>
      <div class="actions">
        <button id="add-sensor" type="button">Add device</button>
        <button id="add-monitored-device" type="button">Add monitored server</button>
        <button type="submit">Save configuration</button>
      </div>
    </form>
    <p id="status" class="status" role="status" aria-live="polite"></p>
  </main>
  <script>
    const sensorList = document.querySelector("#sensor-list");
    const monitoredDeviceList = document.querySelector("#monitored-device-list");
    const status = document.querySelector("#status");
    let deviceTypes = [];

    function addSensor(sensor = {}) {
      const card = document.createElement("fieldset");
      card.className = "sensor";
      card.innerHTML = `
        <label>Device type<select name="device_type" required></select></label>
        <label>Device name<input name="name" required maxlength="100" value=""></label>
        <label>Room name<input name="room_name" required maxlength="100" value=""></label>
        <label>Local IP address<input name="local_ip_address" required inputmode="decimal" placeholder="192.168.1.101" value=""></label>
        <div class="relay-options" hidden>
          <label>Relay mode<select name="relay_mode"><option value="latching">Latching (ON/OFF)</option><option value="pulse">Pulse (automatic OFF)</option></select></label>
          <label class="relay-state-field">Saved state<select name="relay_state"><option value="off">OFF</option><option value="on">ON</option></select></label>
          <label class="pulse-duration-field">Pulse duration (ms)<input name="pulse_duration_ms" type="number" min="1" max="300000" step="1" value="1000"></label>
          <input name="last_pressed_at" type="hidden" value="">
        </div>
        <div class="multi-relay-options" hidden>
          <div class="channel-configs"></div>
        </div>
        <button class="remove" type="button">Remove</button>
      `;
      const typeSelect = card.querySelector('[name="device_type"]');
      for (const type of deviceTypes) {
        const option = document.createElement("option");
        option.value = type.id;
        option.textContent = type.label;
        typeSelect.append(option);
      }
      typeSelect.value = sensor.device_type || "temperature_humidity";
      const relayOptions = card.querySelector(".relay-options");
      const multiRelayOptions = card.querySelector(".multi-relay-options");
      const channelConfigsContainer = card.querySelector(".channel-configs");
      const relayMode = card.querySelector('[name="relay_mode"]');
      relayMode.value = sensor.relay_mode || "latching";
      card.querySelector('[name="relay_state"]').value = sensor.relay_state || "off";
      card.querySelector('[name="pulse_duration_ms"]').value =
        sensor.pulse_duration_ms || 1000;
      card.querySelector('[name="last_pressed_at"]').value =
        sensor.last_pressed_at || "";
      const channelConfigs = sensor.channels || [];
      function createChannelConfig(channelNumber, pin, channel = {}) {
        const fieldset = document.createElement("fieldset");
        fieldset.className = "channel-config";
        const legend = document.createElement("legend");
        legend.textContent = `Channel ${channelNumber} (${pin})`;
        fieldset.append(legend);

        const modeSelect = document.createElement("select");
        modeSelect.name = `channel_${channelNumber}_mode`;
        for (const [value, label] of [
          ["latching", "Latching (ON/OFF)"],
          ["pulse", "Pulse (automatic OFF)"]
        ]) {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          modeSelect.append(option);
        }
        modeSelect.value = channel.relay_mode || "latching";
        modeSelect.addEventListener("change", updateRelayOptions);
        const modeLabel = document.createElement("label");
        modeLabel.textContent = "Mode";
        modeLabel.append(modeSelect);
        fieldset.append(modeLabel);

        const stateSelect = document.createElement("select");
        stateSelect.name = `channel_${channelNumber}_state`;
        for (const [value, label] of [["off", "OFF"], ["on", "ON"]]) {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          stateSelect.append(option);
        }
        stateSelect.value = channel.relay_state || "off";
        const stateLabel = document.createElement("label");
        stateLabel.className = `channel-${channelNumber}-state-field`;
        stateLabel.textContent = "Saved state";
        stateLabel.append(stateSelect);
        fieldset.append(stateLabel);

        const durationInput = document.createElement("input");
        durationInput.name = `channel_${channelNumber}_pulse_duration_ms`;
        durationInput.type = "number";
        durationInput.min = "1";
        durationInput.max = "300000";
        durationInput.step = "1";
        durationInput.value = channel.pulse_duration_ms || 1000;
        const durationLabel = document.createElement("label");
        durationLabel.className = `channel-${channelNumber}-duration-field`;
        durationLabel.textContent = "Pulse duration (ms)";
        durationLabel.append(durationInput);
        fieldset.append(durationLabel);

        const lastPressed = document.createElement("input");
        lastPressed.type = "hidden";
        lastPressed.name = `channel_${channelNumber}_last_pressed_at`;
        lastPressed.value = channel.last_pressed_at || "";
        fieldset.append(lastPressed);
        return fieldset;
      }

      function updateRelayOptions() {
        for (const fieldset of channelConfigsContainer.querySelectorAll(".channel-config")) {
          const number = Number(fieldset.dataset.channel);
          channelConfigs[number - 1] = {
            relay_mode: fieldset.querySelector(`[name="channel_${number}_mode"]`).value,
            relay_state: fieldset.querySelector(`[name="channel_${number}_state"]`).value,
            pulse_duration_ms: Number(
              fieldset.querySelector(`[name="channel_${number}_pulse_duration_ms"]`).value
            ),
            last_pressed_at: fieldset.querySelector(
              `[name="channel_${number}_last_pressed_at"]`
            ).value || null
          };
        }
        const isRelay = typeSelect.value === "relay";
        const selectedType = deviceTypes.find((type) => type.id === typeSelect.value);
        const channelCount = selectedType ? selectedType.channel_count || 0 : 0;
        const isPulse = relayMode.value === "pulse";
        relayOptions.hidden = !isRelay;
        card.querySelector(".relay-state-field").hidden = !isRelay || isPulse;
        card.querySelector(".pulse-duration-field").hidden = !isRelay || !isPulse;
        multiRelayOptions.hidden = channelCount === 0;
        channelConfigsContainer.replaceChildren();
        for (let channelNumber = 1; channelNumber <= channelCount; channelNumber += 1) {
          const channel = channelConfigs[channelNumber - 1] || {};
          const pin = selectedType.channel_pins[channelNumber - 1];
          const fieldset = createChannelConfig(channelNumber, pin, channel);
          fieldset.dataset.channel = channelNumber;
          const mode = fieldset.querySelector(`[name="channel_${channelNumber}_mode"]`).value;
          fieldset.querySelector(`.channel-${channelNumber}-state-field`).hidden =
            mode === "pulse";
          fieldset.querySelector(`.channel-${channelNumber}-duration-field`).hidden =
            mode !== "pulse";
          channelConfigsContainer.append(fieldset);
        }
      }
      typeSelect.addEventListener("change", updateRelayOptions);
      relayMode.addEventListener("change", updateRelayOptions);
      updateRelayOptions();
      card.querySelector('[name="name"]').value = sensor.name || "";
      card.querySelector('[name="room_name"]').value = sensor.room_name || "";
      card.querySelector('[name="local_ip_address"]').value = sensor.local_ip_address || "";
      card.querySelector(".remove").addEventListener("click", () => card.remove());
      sensorList.append(card);
    }

    function addMonitoredDevice(device = {}) {
      const card = document.createElement("fieldset");
      card.className = "monitored-device";
      card.innerHTML = `
        <label>Server name<input name="name" required maxlength="100" value=""></label>
        <label>Local IP address or URL<input name="address" required placeholder="192.168.1.50 or http://server.local" value=""></label>
        <button class="remove" type="button">Remove</button>
      `;
      card.querySelector('[name="name"]').value = device.name || "";
      card.querySelector('[name="address"]').value =
        device.address || device.local_ip_address || "";
      card.querySelector(".remove").addEventListener("click", () => card.remove());
      monitoredDeviceList.append(card);
    }

    async function loadConfiguration() {
      try {
        const [configResponse, typesResponse] = await Promise.all([
          fetch("/api/config", { cache: "no-store" }),
          fetch("/api/device-types", { cache: "no-store" })
        ]);
        const [data, types] = await Promise.all([
          configResponse.json(),
          typesResponse.json()
        ]);
        if (!configResponse.ok) throw new Error(data.error || "Unable to load configuration.");
        if (!typesResponse.ok) throw new Error(types.error || "Unable to load device types.");
        deviceTypes = types.device_types;
        data.sensors.forEach(addSensor);
        (data.monitored_devices || []).forEach(addMonitoredDevice);
      } catch (error) {
        status.className = "status error";
        status.textContent = `Could not load configuration: ${error.message}`;
      }
    }

    document.querySelector("#add-sensor").addEventListener("click", () => addSensor());
    document.querySelector("#add-monitored-device").addEventListener("click", () => addMonitoredDevice());
    document.querySelector("#config-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const sensors = [...sensorList.querySelectorAll(".sensor")].map((card) => {
        const sensor = {
          device_type: card.querySelector('[name="device_type"]').value,
          name: card.querySelector('[name="name"]').value.trim(),
          room_name: card.querySelector('[name="room_name"]').value.trim(),
          local_ip_address: card.querySelector('[name="local_ip_address"]').value.trim()
        };
        if (sensor.device_type === "relay") {
          sensor.relay_mode = card.querySelector('[name="relay_mode"]').value;
          sensor.relay_state = card.querySelector('[name="relay_state"]').value;
          sensor.pulse_duration_ms = Number(
            card.querySelector('[name="pulse_duration_ms"]').value
          );
          sensor.last_pressed_at =
            card.querySelector('[name="last_pressed_at"]').value || null;
        } else if (sensor.device_type !== "temperature_humidity" && sensor.device_type !== "relay") {
          const selectedType = deviceTypes.find((type) => type.id === sensor.device_type);
          sensor.channels = Array.from(
            { length: selectedType.channel_count },
            (_, index) => index + 1
          ).map((channelNumber) => ({
            relay_mode: card.querySelector(`[name="channel_${channelNumber}_mode"]`).value,
            relay_state: card.querySelector(`[name="channel_${channelNumber}_state"]`).value,
            pulse_duration_ms: Number(
              card.querySelector(`[name="channel_${channelNumber}_pulse_duration_ms"]`).value
            ),
            last_pressed_at: card.querySelector(
              `[name="channel_${channelNumber}_last_pressed_at"]`
            ).value || null
          }));
        }
        return sensor;
      });
      const monitoredDevices = [...monitoredDeviceList.querySelectorAll(".monitored-device")]
        .map((card) => ({
          name: card.querySelector('[name="name"]').value.trim(),
          address: card.querySelector('[name="address"]').value.trim()
        }));

      try {
        const response = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sensors, monitored_devices: monitoredDevices })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Unable to save configuration.");
        status.className = "status";
        status.textContent = "Configuration saved.";
      } catch (error) {
        status.className = "status error";
        status.textContent = `Could not save configuration: ${error.message}`;
      }
    });

    loadConfiguration();
  </script>
</body>
</html>
"""


def load_configuration():
    try:
        with CONFIG_PATH.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except OSError as exc:
        raise RuntimeError(f"Could not read {CONFIG_PATH.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{CONFIG_PATH.name} is not valid JSON: {exc}") from exc

    if not isinstance(config, dict):
        raise ValueError(f"{CONFIG_PATH.name} must contain a JSON object.")

    sensors = config.get("sensors")
    if not isinstance(sensors, list):
        raise ValueError(f"{CONFIG_PATH.name} must contain a 'sensors' array.")
    monitored_devices = config.get("monitored_devices", [])
    if not isinstance(monitored_devices, list):
        raise ValueError(
            f"{CONFIG_PATH.name} must contain a 'monitored_devices' array."
        )

    required_fields = ("name", "room_name", "local_ip_address")
    for index, sensor in enumerate(sensors):
        if not isinstance(sensor, dict):
            raise ValueError(f"Sensor at index {index} must be a JSON object.")
        for field in required_fields:
            if not isinstance(sensor.get(field), str) or not sensor[field].strip():
                raise ValueError(
                    f"Sensor at index {index} must have a non-empty '{field}'."
                )
    return {
        "sensors": validate_sensors(sensors),
        "monitored_devices": validate_monitored_devices(monitored_devices),
    }


def load_sensors():
    return load_configuration()["sensors"]


def validate_relay_channel(channel, channel_number):
    if not isinstance(channel, dict):
        raise ValueError(f"Relay channel {channel_number} must be a JSON object.")

    relay_mode = channel.get("relay_mode", "latching")
    relay_state = channel.get("relay_state", "off")
    pulse_duration_ms = channel.get("pulse_duration_ms", 1000)
    last_pressed_at = channel.get("last_pressed_at")

    if not isinstance(relay_mode, str) or relay_mode not in ("latching", "pulse"):
        raise ValueError(
            f"Relay channel {channel_number} mode must be 'latching' or 'pulse'."
        )
    if not isinstance(relay_state, str) or relay_state not in ("on", "off"):
        raise ValueError(
            f"Relay channel {channel_number} state must be 'on' or 'off'."
        )
    if relay_mode == "pulse":
        relay_state = "off"
    if (
        isinstance(pulse_duration_ms, bool)
        or not isinstance(pulse_duration_ms, int)
        or not 1 <= pulse_duration_ms <= 300000
    ):
        raise ValueError(
            f"Relay channel {channel_number} pulse duration must be from 1 to 300000 ms."
        )
    if last_pressed_at is not None:
        if not isinstance(last_pressed_at, str):
            raise ValueError(
                f"Relay channel {channel_number} last_pressed_at must be a timestamp or null."
            )
        try:
            datetime.fromisoformat(last_pressed_at)
        except ValueError as exc:
            raise ValueError(
                f"Relay channel {channel_number} last_pressed_at must be an ISO timestamp."
            ) from exc

    return {
        "relay_mode": relay_mode,
        "relay_state": relay_state,
        "pulse_duration_ms": pulse_duration_ms,
        "last_pressed_at": last_pressed_at,
    }


def validate_sensors(sensors):
    if not isinstance(sensors, list):
        raise ValueError("'sensors' must be an array.")
    if len(sensors) > 100:
        raise ValueError("A maximum of 100 sensors can be configured.")

    validated = []
    for index, sensor in enumerate(sensors):
        if not isinstance(sensor, dict):
            raise ValueError(f"Sensor at index {index} must be a JSON object.")

        clean_sensor = {}
        for field in ("name", "room_name", "local_ip_address"):
            value = sensor.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Sensor at index {index} must have a non-empty '{field}'."
                )
            value = value.strip()
            if len(value) > 100:
                raise ValueError(f"Sensor '{field}' cannot exceed 100 characters.")
            clean_sensor[field] = value

        device_type = sensor.get("device_type", DEFAULT_DEVICE_TYPE)
        if not isinstance(device_type, str) or device_type not in DEVICE_TYPES:
            raise ValueError(f"Sensor at index {index} has an unsupported device type.")
        clean_sensor["device_type"] = device_type
        if device_type == "relay":
            clean_sensor.update(validate_relay_channel(sensor, index + 1))
        elif DEVICE_TYPES[device_type]["mode"] == "relay_channels":
            channels = sensor.get("channels")
            if channels is None:
                channel_count = DEVICE_TYPES[device_type]["channel_count"]
                channels = [{} for _ in range(channel_count)]
            channel_count = DEVICE_TYPES[device_type]["channel_count"]
            if not isinstance(channels, list) or len(channels) != channel_count:
                raise ValueError(
                    f"{DEVICE_TYPES[device_type]['label']} at index {index} must have exactly {channel_count} channels."
                )
            clean_sensor["channels"] = [
                validate_relay_channel(channel, channel_number)
                for channel_number, channel in enumerate(channels, start=1)
            ]

        try:
            address = ipaddress.IPv4Address(clean_sensor["local_ip_address"])
        except ipaddress.AddressValueError as exc:
            raise ValueError(
                f"Sensor at index {index} must have a valid IPv4 address."
            ) from exc
        if not address.is_private:
            raise ValueError(
                f"Sensor at index {index} must use a private IPv4 address."
            )
        validated.append(clean_sensor)

    return validated


def validate_monitored_devices(devices):
    if not isinstance(devices, list):
        raise ValueError("'monitored_devices' must be an array.")
    if len(devices) > 100:
        raise ValueError("A maximum of 100 monitored devices can be configured.")

    validated = []
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            raise ValueError(
                f"Monitored device at index {index} must be a JSON object."
            )
        name = device.get("name")
        address = device.get("address", device.get("local_ip_address"))
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"Monitored device at index {index} must have a non-empty 'name'."
            )
        name = name.strip()
        if len(name) > 100:
            raise ValueError("Monitored device names cannot exceed 100 characters.")
        if not isinstance(address, str) or not address.strip():
            raise ValueError(
                f"Monitored device at index {index} must have a non-empty 'address'."
            )
        address = address.strip()
        if len(address) > 2048 or any(ord(character) < 32 for character in address):
            raise ValueError(
                f"Monitored device address at index {index} is invalid or too long."
            )
        try:
            parsed_address = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            parsed_address = None
        if parsed_address is not None:
            if not parsed_address.is_private:
                raise ValueError(
                    f"Monitored device at index {index} must use a private IPv4 address."
                )
            normalized_address = str(parsed_address)
        else:
            try:
                parsed_url = urlsplit(address)
                port = parsed_url.port
            except ValueError as exc:
                raise ValueError(
                    f"Monitored device at index {index} must have a valid HTTP or HTTPS URL."
                ) from exc
            if (
                parsed_url.scheme not in ("http", "https")
                or not parsed_url.hostname
                or parsed_url.username is not None
                or parsed_url.password is not None
                or parsed_url.fragment
                or (port is not None and not 1 <= port <= 65535)
            ):
                raise ValueError(
                    f"Monitored device at index {index} must use a valid HTTP or HTTPS URL without credentials or a fragment."
                )
            try:
                url_ip = ipaddress.ip_address(parsed_url.hostname)
            except ValueError:
                url_ip = None
            if url_ip is not None and not url_ip.is_private:
                raise ValueError(
                    f"Monitored device URL at index {index} must use a private IP address."
                )
            normalized_address = address
        validated.append({"name": name, "address": normalized_address})
    return validated


def read_monitored_device(device):
    address = device["address"]
    try:
        parsed_address = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        parsed_address = None
    if parsed_address is None:
        try:
            parsed_url = urlsplit(address)
            port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
            resolved_addresses = {
                ipaddress.ip_address(result[4][0].split("%", 1)[0])
                for result in socket.getaddrinfo(
                    parsed_url.hostname,
                    port,
                    type=socket.SOCK_STREAM,
                )
            }
            if not resolved_addresses or any(
                not resolved_address.is_private
                for resolved_address in resolved_addresses
            ):
                return {
                    **device,
                    "status": "offline",
                    "error": "URL does not resolve exclusively to private addresses.",
                }
            opener = build_opener(NoRedirectHandler)
            with opener.open(Request(address, method="GET"), timeout=2):
                return {**device, "status": "available"}
        except HTTPError:
            return {**device, "status": "available"}
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            return {**device, "status": "offline", "error": str(exc)}
    if os.name == "nt":
        command = ["ping", "-n", "1", "-w", "1000", str(parsed_address)]
    else:
        command = ["ping", "-c", "1", "-W", "1", str(parsed_address)]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {**device, "status": "offline", "error": "Ping timed out."}
    except OSError as exc:
        return {**device, "status": "offline", "error": f"Could not run ping: {exc}"}
    return {
        **device,
        "status": "available" if result.returncode == 0 else "offline",
    }


def read_sensor(sensor):
    device_type = DEVICE_TYPES[sensor["device_type"]]
    if device_type["mode"] == "relay_channels":
        channels = []
        for channel_number, config in enumerate(sensor["channels"], start=1):
            try:
                reading = read_relay_status(sensor, channel_number)
                channels.append({
                    **config,
                    "channel": channel_number,
                    "relay_state": reading["state"],
                    "pulse_remaining_ms": reading.get("remaining_ms", 0),
                    "status": "online",
                })
            except (RelayCommunicationError, ValueError) as exc:
                channels.append({
                    **config,
                    "channel": channel_number,
                    "status": "offline",
                    "error": str(exc),
                })
        return {
            **sensor,
            "device_type_label": device_type["label"],
            "channels": channels,
            "status": "online" if any(
                channel["status"] == "online" for channel in channels
            ) else "offline",
        }

    if device_type["mode"] == "relay":
        try:
            reading = read_relay_status(sensor)
            return {
                **sensor,
                "device_type_label": device_type["label"],
                "relay_state": reading["state"],
                "pulse_remaining_ms": reading.get("remaining_ms", 0),
                "status": "online",
            }
        except (RelayCommunicationError, ValueError) as exc:
            return {
                **sensor,
                "device_type_label": device_type["label"],
                "status": "offline",
                "error": str(exc),
            }

    if device_type["mode"] != "temperature_humidity":
        return {
            **sensor,
            "device_type_label": device_type["label"],
            "temperature": None,
            "humidity": None,
            "status": "configured",
        }

    try:
        address = str(ipaddress.IPv4Address(sensor["local_ip_address"]))
        if not ipaddress.IPv4Address(address).is_private:
            raise ValueError("Sensor address must be a private IPv4 address.")
        with urlopen(f"http://{address}/api/temperature", timeout=2) as response:
            reading = json.load(response)
        if not isinstance(reading, dict):
            raise ValueError("Sensor response must be a JSON object.")

        temperature = reading.get("temperature")
        humidity = reading.get("humidity")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
        ):
            raise ValueError("Sensor response has no numeric temperature.")
        if (
            isinstance(humidity, bool)
            or not isinstance(humidity, (int, float))
            or not math.isfinite(humidity)
        ):
            raise ValueError("Sensor response has no numeric humidity.")

        return {
            **sensor,
            "device_type_label": device_type["label"],
            "temperature": round(temperature, 1),
            "humidity": round(humidity, 1),
            "status": "online",
        }
    except (RelayCommunicationError, HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return {
            **sensor,
            "device_type_label": device_type["label"],
            "temperature": None,
            "humidity": None,
            "status": "offline",
            "error": str(exc),
        }


def save_configuration(sensors, monitored_devices):
    temporary_path = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    temporary_path.write_text(
        json.dumps(
            {"sensors": sensors, "monitored_devices": monitored_devices},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(CONFIG_PATH)


def save_sensor_config(sensors):
    monitored_devices = load_configuration()["monitored_devices"]
    save_configuration(sensors, monitored_devices)


def relay_url(sensor, channel_number=None):
    address = str(ipaddress.IPv4Address(sensor["local_ip_address"]))
    if not ipaddress.IPv4Address(address).is_private:
        raise ValueError("Relay address must be a private IPv4 address.")
    if DEVICE_TYPES[sensor["device_type"]]["mode"] == "relay_channels":
        channel_count = DEVICE_TYPES[sensor["device_type"]]["channel_count"]
        if channel_number is None or not 1 <= channel_number <= channel_count:
            raise ValueError("Relay channel number is out of range.")
        return f"http://{address}/api/relay/{channel_number}"
    return f"http://{address}/api/relay"


def read_relay_status(sensor, channel_number=None):
    try:
        with urlopen(relay_url(sensor, channel_number), timeout=3) as response:
            reading = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RelayCommunicationError(f"Could not read relay status: {exc}") from exc
    if not isinstance(reading, dict) or reading.get("state") not in ("on", "off"):
        raise RelayCommunicationError("Relay returned an invalid state.")
    return reading


def send_relay_command(sensor, command, channel_number=None):
    request = Request(
        relay_url(sensor, channel_number),
        data=json.dumps(command).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RelayCommunicationError(f"Could not send relay command: {exc}") from exc
    if not isinstance(result, dict) or result.get("state") not in ("on", "off"):
        raise RelayCommunicationError("Relay returned an invalid state.")
    return result


def activate_relay(index, channel_number=None):
    sensors = load_sensors()
    if index < 0 or index >= len(sensors):
        raise ValueError("Device was not found.")
    sensor = sensors[index]
    if DEVICE_TYPES[sensor["device_type"]]["mode"] not in ("relay", "relay_channels"):
        raise ValueError("Selected device is not a relay.")

    if DEVICE_TYPES[sensor["device_type"]]["mode"] == "relay_channels":
        channel_count = DEVICE_TYPES[sensor["device_type"]]["channel_count"]
        if channel_number is None or not 1 <= channel_number <= channel_count:
            raise ValueError("Relay channel number is out of range.")
        channel = sensor["channels"][channel_number - 1]
    else:
        if channel_number is not None:
            raise ValueError("Single-channel relay does not accept a channel number.")
        channel = sensor

    current = read_relay_status(sensor, channel_number)
    if channel["relay_mode"] == "pulse":
        if current["state"] == "on":
            command = {"mode": "latching", "state": "off"}
        else:
            command = {
                "mode": "pulse",
                "duration_ms": channel["pulse_duration_ms"],
            }
    else:
        command = {
            "mode": "latching",
            "state": "off" if current["state"] == "on" else "on",
        }

    result = send_relay_command(sensor, command, channel_number)
    channel["relay_state"] = (
        result["state"] if channel["relay_mode"] == "latching" else "off"
    )
    channel["last_pressed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if DEVICE_TYPES[sensor["device_type"]]["mode"] == "relay_channels":
        sensor["channels"][channel_number - 1] = channel
    else:
        sensor.update(channel)
    sensors[index] = sensor
    save_sensor_config(sensors)
    return {
        "state": result["state"],
        "last_pressed_at": channel["last_pressed_at"],
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def respond(self, status_code, content_type, body):
        encoded_body = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded_body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded_body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.respond(200, "text/html; charset=utf-8", PAGE)
            return
        if path == "/settings":
            self.respond(200, "text/html; charset=utf-8", SETTINGS_PAGE)
            return
        if path == "/api/config":
            try:
                body = json.dumps(load_configuration())
            except (RuntimeError, ValueError) as exc:
                body = json.dumps({"error": str(exc)})
                self.respond(500, "application/json; charset=utf-8", body)
                return
            self.respond(200, "application/json; charset=utf-8", body)
            return
        if path == "/api/servers":
            try:
                devices = load_configuration()["monitored_devices"]
                with ThreadPoolExecutor(max_workers=min(16, len(devices) or 1)) as executor:
                    servers = list(executor.map(read_monitored_device, devices))
                body = json.dumps({"servers": servers})
            except (RuntimeError, ValueError) as exc:
                body = json.dumps({"error": str(exc)})
                self.respond(500, "application/json; charset=utf-8", body)
                return
            self.respond(200, "application/json; charset=utf-8", body)
            return
        if path == "/api/device-types":
            body = json.dumps({
                "device_types": [
                    {
                        "id": device_type,
                        "label": details["label"],
                        "channel_count": details.get("channel_count", 0),
                        "channel_pins": details.get("channel_pins", []),
                    }
                    for device_type, details in DEVICE_TYPES.items()
                ]
            })
            self.respond(200, "application/json; charset=utf-8", body)
            return
        if path == "/api/sensors":
            try:
                sensors = [
                    {**read_sensor(sensor), "index": index}
                    for index, sensor in enumerate(load_sensors())
                ]
                body = json.dumps({"sensors": sensors})
            except (RuntimeError, ValueError) as exc:
                body = json.dumps({"error": str(exc)})
                self.respond(500, "application/json; charset=utf-8", body)
                return
            self.respond(200, "application/json; charset=utf-8", body)
            return
        self.respond(404, "text/plain; charset=utf-8", "Not found")

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith(RELAY_ROUTE_PREFIX):
            if self.headers.get_content_type() != "application/json":
                self.respond(
                    415,
                    "application/json; charset=utf-8",
                    json.dumps({"error": "Content-Type must be application/json."}),
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length", ""))
                if content_length < 0 or content_length > 1024:
                    raise ValueError("Request body must be no larger than 1 KB.")
                payload = json.loads(self.rfile.read(content_length))
                if not isinstance(payload, dict) or payload.get("action") != "activate":
                    raise ValueError("Relay action must be 'activate'.")
            except (ValueError, json.JSONDecodeError) as exc:
                self.respond(
                    400,
                    "application/json; charset=utf-8",
                    json.dumps({"error": str(exc)}),
                )
                return

            route_parts = path[len(RELAY_ROUTE_PREFIX):].split("/")
            if (
                len(route_parts) not in (1, 2)
                or not all(part.isdecimal() for part in route_parts)
            ):
                self.respond(404, "text/plain; charset=utf-8", "Not found")
                return
            device_index = int(route_parts[0])
            channel_number = int(route_parts[1]) if len(route_parts) == 2 else None
            try:
                result = activate_relay(device_index, channel_number)
            except ValueError as exc:
                self.respond(
                    400,
                    "application/json; charset=utf-8",
                    json.dumps({"error": str(exc)}),
                )
                return
            except RelayCommunicationError as exc:
                self.respond(
                    502,
                    "application/json; charset=utf-8",
                    json.dumps({"error": f"Relay command failed: {exc}"}),
                )
                return
            except OSError as exc:
                self.respond(
                    500,
                    "application/json; charset=utf-8",
                    json.dumps({"error": f"Could not save relay state: {exc}"}),
                )
                return
            self.respond(
                200,
                "application/json; charset=utf-8",
                json.dumps(result),
            )
            return

        if path != "/api/config":
            self.respond(404, "text/plain; charset=utf-8", "Not found")
            return
        if self.headers.get_content_type() != "application/json":
            self.respond(
                415,
                "application/json; charset=utf-8",
                json.dumps({"error": "Content-Type must be application/json."}),
            )
            return

        try:
            content_length = int(self.headers.get("Content-Length", ""))
            if content_length < 0 or content_length > 65536:
                raise ValueError("Request body must be no larger than 64 KB.")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            sensors = validate_sensors(payload.get("sensors"))
            monitored_devices = (
                validate_monitored_devices(payload["monitored_devices"])
                if "monitored_devices" in payload
                else load_configuration()["monitored_devices"]
            )
        except (ValueError, json.JSONDecodeError) as exc:
            body = json.dumps({"error": str(exc)})
            self.respond(400, "application/json; charset=utf-8", body)
            return

        try:
            save_configuration(sensors, monitored_devices)
        except OSError as exc:
            body = json.dumps({"error": f"Could not save configuration: {exc}"})
            self.respond(500, "application/json; charset=utf-8", body)
            return

        self.respond(
            200,
            "application/json; charset=utf-8",
            json.dumps({"saved": True}),
        )


def main():
    parser = argparse.ArgumentParser(description="Run the local Wi-Fi sensor dashboard.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Dashboard running at http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
