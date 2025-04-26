#!/usr/bin/env python3
import os
import json
import asyncio
import paho.mqtt.client as mqtt
from bleak import BleakClient, BleakScanner

# ————————————
# CONFIGURATION & STATE STORAGE
# ————————————

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.json')

# Load or initialize config
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE) as f:
        config = json.load(f)
else:
    config = {}

# If first run, interactively gather settings
if 'mac' not in config:
    use_scan = input("No device MAC in config. Scan for BLE device? [Y/n]: ").strip().lower()
    if use_scan in ['', 'y', 'yes']:
        config['_need_scan'] = True
    else:
        config['mac'] = input("Enter device MAC address (e.g. AA:BB:CC:DD:EE:FF): ").strip()

if 'mqtt_enabled' not in config:
    use_mqtt = input("Enable MQTT? [y/N]: ").strip().lower()
    config['mqtt_enabled'] = use_mqtt in ['y', 'yes']

if config.get('mqtt_enabled') and 'mqtt' not in config:
    print("Configure MQTT broker settings:")
    config['mqtt'] = {
        'broker': input("  Broker address (IP/hostname): ").strip(),
        'port': int(input("  Port [1883]: ") or 1883),
        'username': input("  Username (leave blank if none): ").strip(),
        'password': input("  Password (leave blank if none): ").strip(),
    }

# Persist any new settings
with open(CONFIG_FILE, 'w') as f:
    json.dump(config, f, indent=4)

# Extract config
MAC = config.get('mac')
MQTT_ENABLED = config.get('mqtt_enabled', False)
MQTT_CFG = config.get('mqtt', {})

# MQTT setup
if MQTT_ENABLED:
    mqtt_client = mqtt.Client("bms_reader")
    if MQTT_CFG.get('username'):
        mqtt_client.username_pw_set(MQTT_CFG['username'], MQTT_CFG['password'])
    mqtt_client.connect(MQTT_CFG['broker'], MQTT_CFG['port'])

# BLE service UUID
NUS_SERVICE = "6e400000-b5a3-f393-e0a9-e50e24dcca9e"

# CRC16 helper
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if (crc & 1) else 0)
    return crc & 0xFFFF

# Build protocol frame
def build_frame(cmd: int, payload: bytes = b"", address: int = 0) -> bytes:
    body = bytearray([address, 0, 0, (cmd >> 8) & 0xFF, cmd & 0xFF]) + payload
    body[1] = len(body)
    crc = crc16(body)
    return bytes([0x7A]) + body + bytes([crc >> 8, crc & 0xFF, 0xA7])

def build_read1_frame() -> bytes:
    return build_frame(0x0001)

# Async scan helper
def scan_for_device(timeout: float = 5.0) -> None:
    """Marks config to scan on next loop."""
    pass  # placeholder

async def select_device(timeout: float = 5.0) -> str:
    print(f"Scanning for BLE devices for {timeout}s…")
    devices = await BleakScanner.discover(timeout=timeout)
    if not devices:
        raise RuntimeError("No BLE devices found.")
    for idx, d in enumerate(devices):
        print(f"{idx}: {d.name or 'Unknown':25} [{d.address}]")
    while True:
        choice = input("Select device index: ")
        if choice.isdigit() and 0 <= int(choice) < len(devices):
            return devices[int(choice)].address
        print("Invalid choice, try again.")

# Main logic
def main():
    async def _main():
        global MAC
        # Perform scan first-run if needed
        if config.get('_need_scan'):
            MAC = await select_device()
            config['mac'] = MAC
            config.pop('_need_scan', None)
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)

        print(f"Using device MAC: {MAC}")
        finished = asyncio.Event()

        async with BleakClient(MAC) as client:
            print(f"Connecting to {MAC}…")
            nus = client.services.get_service(NUS_SERVICE)
            if not nus:
                print("NUS service not found; check UUID.")
                return
            tx = next(c.uuid for c in nus.characteristics if any(p.startswith("write") for p in c.properties))
            rx = next(c.uuid for c in nus.characteristics if "notify" in c.properties)
            print(f"TX: {tx}, RX: {rx}")

            def handler(_, data: bytearray):
                payload = data[6:6 + (data[2] - 4)]
                idx = 0
                def get8():
                    nonlocal idx; v = payload[idx]; idx+=1; return v
                def get16():
                    nonlocal idx; v = (payload[idx]<<8)|payload[idx+1]; idx+=2; return v

                # Decode fields
                results = {}
                results['online_status'] = get8()
                n = get8(); results['batteries_series_number'] = n
                for i in range(n):
                    results[f'cell_{i+1}_voltage'] = (get16() & 0x7FFF)/1000.0
                results['max_cell_number'] = get8()
                results['max_cell_voltage'] = get16()/1000.0
                results['min_cell_number'] = get8()
                results['min_cell_voltage'] = get16()/1000.0
                results['total_current'] = (get16()/100.0) - 300.0
                results['soc'] = get16()/100.0
                results['soh'] = get16()/100.0
                results['actual_capacity'] = get16()/100.0
                results['surplus_capacity'] = get16()/100.0
                results['nominal_capacity'] = get16()/100.0
                t_n = get8(); results['batteries_temperature_number'] = t_n
                for i in range(t_n):
                    results[f'cell_temperature_{i+1}'] = (get16() & 0x7FFF) - 50
                results['environmental_temperature'] = get16() - 50
                results['pcb_temperature'] = get16() - 50
                results['max_temperature_cell_number'] = get8()
                results['max_temperature_cell_value'] = (get8() & 0x7FFF) - 50
                results['min_temperature_cell_number'] = get8()
                results['min_temperature_cell_value'] = (get8() & 0x7FFF) - 50
                results['fault1'] = get8()
                results['fault2'] = get8()
                results['alert1'] = get8()
                results['alert2'] = get8()
                results['alert3'] = get8()
                results['alert4'] = get8()
                results['cycle_index'] = get16()
                results['total_voltage'] = get16()/100.0
                results['status'] = get8()
                results['total_charging_capacity'] = get16()
                results['total_discharge_capacity'] = get16()
                results['total_recharge_time'] = get16()
                results['total_discharge_time'] = get16()
                results['battery_type'] = get8()

                # Output or publish
                if MQTT_ENABLED:
                    for key, val in results.items():
                        mqtt_client.publish(f"home/bms/{key}", val)
                else:
                    for key, val in results.items():
                        print(f"{key}: {val}")

                finished.set()

            await client.start_notify(rx, handler)
            await asyncio.sleep(1.5)
            frame = build_read1_frame()
            print(f"Sending read1 frame: {frame.hex()}")
            await client.write_gatt_char(tx, frame)
            await finished.wait()
            await client.stop_notify(rx)

    asyncio.run(_main())

if __name__ == "__main__":
    main()
