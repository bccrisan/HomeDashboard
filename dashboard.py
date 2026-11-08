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
DEVICE_TYPES_PATH = Path(__file__).with_name("device-types.json")
DEFAULT_DEVICE_TYPE = "temperature_humidity"
RELAY_ROUTE_PREFIX = "/api/relay/"


def load_device_types():
    try:
        device_types = json.loads(DEVICE_TYPES_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Could not read {DEVICE_TYPES_PATH.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{DEVICE_TYPES_PATH.name} is not valid JSON: {exc}") from exc

    if not isinstance(device_types, dict) or DEFAULT_DEVICE_TYPE not in device_types:
        raise ValueError(
            f"{DEVICE_TYPES_PATH.name} must be an object containing '{DEFAULT_DEVICE_TYPE}'."
        )
    for device_type, details in device_types.items():
        if (
            not isinstance(device_type, str)
            or not isinstance(details, dict)
            or not isinstance(details.get("label"), str)
            or details.get("mode") not in ("temperature_humidity", "relay", "relay_channels")
        ):
            raise ValueError(f"{DEVICE_TYPES_PATH.name} has an invalid device type entry.")
        if details["mode"] == "relay_channels":
            pins = details.get("channel_pins")
            count = details.get("channel_count")
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 1
                or not isinstance(pins, list)
                or len(pins) != count
                or not all(isinstance(pin, str) and pin for pin in pins)
            ):
                raise ValueError(
                    f"{DEVICE_TYPES_PATH.name} has invalid channel metadata for '{device_type}'."
                )
    return device_types


DEVICE_TYPES = load_device_types()


class RelayCommunicationError(Exception):
    pass


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


PAGE = (
    Path(__file__).with_name("templates") / "dashboard.html"
).read_text(encoding="utf-8")

SETTINGS_PAGE = (
    Path(__file__).with_name("templates") / "settings.html"
).read_text(encoding="utf-8")


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
