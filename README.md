# HomeDashbord
Just a typical dashboard that integrates a few local IOTs, the catch, it's light and low power

## Wi-Fi sensor dashboard

The Python dashboard polls temperature and humidity sensors at `http://<local-ip>/api/temperature`. The included ESP8266 firmware provides this endpoint for a NodeMCU with a DHT11 sensor connected to D6. A separate single-channel relay firmware is provided at `firmware/esp8266-1ch-relay/esp8266-1ch-relay.ino`. The addresses in `wifi-sensors.json` are examples; replace them with the addresses printed by the boards and remove any devices you do not have.

The configuration page supports **Temperature and humidity sensor**, **1 Channel relay**, **2 Channel relay**, **4 Channel relay**, and **8 Channel relay (74HC595N)** device types. Relay channels can each be configured for latching ON/OFF or pulse mode with an independent pulse duration. Dashboard buttons show blue when a channel is OFF and red when ON; each channel's last-pressed time is saved in `wifi-sensors.json`. Multi-channel device types define their channel count and pins in the `DEVICE_TYPES` registry in `dashboard.py`, which drives both the configuration page and dashboard. Add a registry entry there to make another type available in the selector, then add its device-specific firmware/API behavior as needed.

The dashboard's left-side **Local servers** panel displays separately configured monitored devices as **Available** or **Offline**. Add or remove these under **Monitored devices** in Device Configuration; this list is stored separately from sensors in the `monitored_devices` array in `wifi-sensors.json`. Enter a private IPv4 address to check the server with ping, or a full local `http://` or `https://` URL (including an optional port and path) to check it with an HTTP GET request. Any HTTP response means the URL is available, including an HTTP error response; redirects are not followed. URLs should point to local servers. A firewall that blocks ping can make an IP-monitored server appear offline.

### Flash the ESP8266 firmware

1. In Arduino IDE, install the ESP8266 board package and select your NodeMCU ESP8266 board.
2. Install the **DHT sensor library** by Adafruit (and its Adafruit Unified Sensor dependency).
3. Copy `firmware/esp8266-dht11/secrets.h.example` to `firmware/esp8266-dht11/secrets.h` and enter your Wi-Fi name and password.
4. Open `firmware/esp8266-dht11/esp8266-dht11.ino`, connect the DHT11 data pin to D6, power it from 3.3 V, connect ground to ground, and upload it. ESP8266 GPIO pins are not 5 V tolerant.
5. Open Serial Monitor at 115200 baud. Copy the printed sensor IP into the matching `local_ip_address` in `wifi-sensors.json`. Keep the computer running the dashboard and the ESP8266 on the same local network.

The firmware uses DHCP, so its address can change if your router assigns a new lease. Update the JSON address when that happens, or configure a DHCP reservation in your router. The sensor endpoint is unauthenticated and intended for a trusted local network; do not expose it directly to the internet.

The firmware returns a JSON object such as `{"temperature":23.0,"humidity":45.0}` from `/api/temperature`.

### Flash the single-channel relay firmware

1. In Arduino IDE, install the ESP8266 board package and select your NodeMCU ESP8266 board.
2. Install **ArduinoJson** (version 6 or later) through Library Manager.
3. Copy `firmware/esp8266-1ch-relay/secrets.h.example` to `firmware/esp8266-1ch-relay/secrets.h` and enter your Wi-Fi name and password.
4. Open `firmware/esp8266-1ch-relay/esp8266-1ch-relay.ino`, connect the relay module's logic input to D0, and upload it. Use a suitable supply for the relay module and share ground with the NodeMCU; do not power a relay coil directly from a GPIO pin.
5. Open Serial Monitor at 115200 baud and use the printed IP address to send commands from a device on the same local network.

The relay API is `http://<relay-ip>/api/relay`. Send JSON POST requests to switch modes:

```json
{"mode":"latching","state":"on"}
{"mode":"latching","state":"off"}
{"mode":"pulse","duration_ms":1000}
```

Latching commands keep the relay in the requested state until the next command. A pulse command turns it on for the requested duration, then turns it off. Pulse duration must be from 1 to 300000 ms. `GET /api/relay` reports the current state and remaining pulse time. The firmware drives D0 LOW during startup. It assumes a common active-low relay input; with that setting, LOW energizes the relay at startup. Set `RELAY_ACTIVE_LOW` to `false` for an active-high module, where LOW leaves the relay off. This unauthenticated API is intended only for a trusted local network; do not expose it directly to the internet.

### Flash the two-channel relay firmware

1. In Arduino IDE, install the ESP8266 board package, select your NodeMCU ESP8266 board, and install **ArduinoJson** version 6 or later.
2. Copy `firmware/esp8266-2ch-relay/secrets.h.example` to `firmware/esp8266-2ch-relay/secrets.h` and enter your Wi-Fi name and password.
3. Open `firmware/esp8266-2ch-relay/esp8266-2ch-relay.ino`. Connect channel 1's logic input to D0 and channel 2's logic input to D1. Use a suitable relay-module supply and common ground; do not power relay coils directly from GPIO pins.
4. Upload the sketch and open Serial Monitor at 115200 baud. Add the printed IP as a **2 Channel relay** in Device Configuration.

The firmware exposes `GET` and `POST` endpoints at `/api/relay/1` and `/api/relay/2`. Each channel accepts the same latching or pulse JSON commands as the single-channel firmware. The dashboard stores each channel's mode, saved state, pulse duration, and last-pressed timestamp independently. At startup both pins are driven LOW; with the default active-low relay setting, that energizes both relay channels. Set `RELAY_ACTIVE_LOW` to `false` for active-high relay modules. The unauthenticated API is intended only for a trusted local network; do not expose it directly to the internet.

### Flash the four-channel relay firmware

1. In Arduino IDE, install the ESP8266 board package, select your NodeMCU ESP8266 board, and install **ArduinoJson** version 6 or later.
2. Copy `firmware/esp8266-4ch-relay/secrets.h.example` to `firmware/esp8266-4ch-relay/secrets.h` and enter your Wi-Fi name and password.
3. Open `firmware/esp8266-4ch-relay/esp8266-4ch-relay.ino`. Connect relay channel inputs 1-4 to D0, D1, D2, and D5, respectively. Use a suitable relay-module supply and common ground; do not power relay coils directly from GPIO pins.
4. Upload the sketch and open Serial Monitor at 115200 baud. Add its printed IP as a **4 Channel relay** in Device Configuration.

The firmware exposes separate `GET` and `POST` endpoints at `/api/relay/1` through `/api/relay/4`; each accepts the same latching or pulse commands as the other relay firmware. Device Configuration saves each channel's mode, state, pulse duration, and last-pressed timestamp independently, and the dashboard displays one control button per channel. At startup, all four pins are driven LOW. With the default active-low relay setting, that energizes all four relay channels; set `RELAY_ACTIVE_LOW` to `false` for active-high modules. D0 is GPIO16; D1, D2, and D5 are used for the other channels. Do not substitute ESP8266 boot-strap pins without checking the relay module's electrical behavior during reset. The unauthenticated API is intended only for a trusted local network; do not expose it directly to the internet.

### Flash the eight-channel relay firmware

1. In Arduino IDE, install the ESP8266 board package, select your NodeMCU ESP8266 board, and install **ArduinoJson** version 6 or later.
2. Copy `firmware/esp8266-8ch-relay/secrets.h.example` to `firmware/esp8266-8ch-relay/secrets.h` and enter your Wi-Fi name and password.
3. Open `firmware/esp8266-8ch-relay/esp8266-8ch-relay.ino`. Connect the 74HC595N `DS` (pin 14), `SH_CP` (pin 11), `ST_CP` (pin 12), and `/OE` (pin 13) to NodeMCU D7, D6, D5, and D0, respectively. Connect `/MR` (pin 10) to 3.3 V, `VCC` (pin 16) to 3.3 V, and `GND` (pin 8) to ground. Add a pull-up from `/OE` to 3.3 V so the outputs remain disabled while the ESP8266 boots; firmware enables them after setting the initial output state. Connect Q0-Q7 (pins 15, 1, 2, 3, 4, 5, 6, 7) to relay-module inputs 1-8, and share ground.
4. Do not connect relay coils directly to the 74HC595N. Verify the relay module's input voltage and current requirements; use a suitable transistor/driver array or level shifter between the shift register and module inputs when needed. Use a properly rated relay supply.
5. Upload the sketch and open Serial Monitor at 115200 baud. Add its printed IP as an **8 Channel relay (74HC595N)** in Device Configuration.

The firmware exposes separate `GET` and `POST` endpoints at `/api/relay/1` through `/api/relay/8`. Each channel has independent latching or pulse mode, and its settings and last-pressed timestamp are saved independently by the dashboard. With the default active-low setting, the initial shift-register outputs are LOW and all channels are energized after `/OE` is enabled. Set `RELAY_ACTIVE_LOW` to `false` for active-high relay inputs. The unauthenticated API is intended only for a trusted local network; do not expose it directly to the internet.

Create and activate a virtual environment on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Run the dashboard:

```powershell
python dashboard.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Use the **Configuration** button at the bottom right to add, edit, or remove devices; saving writes the changes to `wifi-sensors.json`. Device names and room names are required, and device addresses must be private IPv4 addresses. The dashboard uses only Python's standard library, so no additional packages are needed. Press `Ctrl+C` in the terminal to stop it. Sensors and relays are refreshed every 30 seconds; active relay pulses are checked twice per second until they switch off.
