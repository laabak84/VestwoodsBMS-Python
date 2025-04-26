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

# MQTT Quality of Service
QOS = 1  # 0 = at most once, 1 = at least once, 2 = exactly once

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
    mqtt_client.loop_start()  # Start network loop in background

    def publish(topic, value, retain=False):
        mqtt_client.publish(topic, value, qos=QOS, retain=retain)
else:
    def publish(topic, value, retain=False):
        print(f"{topic}: {value}")

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

async def select_device(timeout: float = 5.0) -> str:
    print(f"Scanning for BLE devices for {timeout}s…")
    devs = await BleakScanner.discover(timeout=timeout)
    for i, d in enumerate(devs):
        print(f"{i}: {d.name or 'Unknown':25} [{d.address}]")
    idx = int(input("Select device index: "))
    return devs[idx].address

async def main():
    global MAC
    if config.get('_need_scan'):
        MAC = await select_device()
        config['mac'] = MAC
        config.pop('_need_scan', None)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)

    print(f"Using MAC: {MAC}")
    finished = asyncio.Event()

    async with BleakClient(MAC) as client:
        print(f"Connecting to {MAC}…")
        nus = client.services.get_service(NUS_SERVICE)
        if not nus:
            print("NUS service not found; check UUID.")
            return
        tx = next(c.uuid for c in nus.characteristics if any(p.startswith("write") for p in c.properties))
        rx = next(c.uuid for c in nus.characteristics if "notify" in c.properties)

        def handler(_, data: bytearray):
            payload = data[6:6 + (data[2] - 4)]
            idx = 0
            def get8():
                nonlocal idx; v = payload[idx]; idx+=1; return v
            def get16():
                nonlocal idx; v = (payload[idx]<<8)|payload[idx+1]; idx+=2; return v

            # publish each field via wrapper
            publish("home/bms/online_status", get8())
            n = get8(); publish("home/bms/batteries_series_number", n)
            for i in range(n):
                raw = get16() & 0x7FFF; publish(f"home/bms/cell_{i+1}_voltage", raw/1000.0)
            publish("home/bms/max_cell_number", get8())
            publish("home/bms/max_cell_voltage", get16()/1000.0)
            publish("home/bms/min_cell_number", get8())
            publish("home/bms/min_cell_voltage", get16()/1000.0)
            publish("home/bms/total_current", (get16()/100.0)-300.0)
            publish("home/bms/soc", get16()/100.0)
            publish("home/bms/soh", get16()/100.0)
            publish("home/bms/actual_capacity", get16()/100.0)
            publish("home/bms/surplus_capacity", get16()/100.0)
            publish("home/bms/nominal_capacity", get16()/100.0)
            t_n = get8(); publish("home/bms/batteries_temperature_number", t_n)
            for i in range(t_n):
                rawt = get16() & 0x7FFF; publish(f"home/bms/cell_temperature_{i+1}", rawt-50)
            publish("home/bms/environmental_temperature", get16()-50)
            publish("home/bms/pcb_temperature", get16()-50)
            publish("home/bms/max_temperature_cell_number", get8())
            publish("home/bms/max_temperature_cell_value", (get8() & 0x7FFF)-50)
            publish("home/bms/min_temperature_cell_number", get8())
            publish("home/bms/min_temperature_cell_value", (get8() & 0x7FFF)-50)
            publish("home/bms/fault1", get8())
            publish("home/bms/fault2", get8())
            publish("home/bms/alert1", get8())
            publish("home/bms/alert2", get8())
            publish("home/bms/alert3", get8())
            publish("home/bms/alert4", get8())
            publish("home/bms/cycle_index", get16())
            publish("home/bms/total_voltage", get16()/100.0)
            publish("home/bms/status", get8())
            publish("home/bms/total_charging_capacity", get16())
            publish("home/bms/total_discharge_capacity", get16())
            publish("home/bms/total_recharge_time", get16())
            publish("home/bms/total_discharge_time", get16())
            publish("home/bms/battery_type", get8())

            finished.set()

        await client.start_notify(rx, handler)
        await asyncio.sleep(1.5)
        frame = build_read1_frame()
        print("Sending read1:", frame.hex())
        await client.write_gatt_char(tx, frame)
        await finished.wait()
        await client.stop_notify(rx)

    # Ensure MQTT messages are sent before exiting
    if MQTT_ENABLED:
        await asyncio.sleep(0.5)
        mqtt_client.loop_stop()

if __name__ == "__main__":
    asyncio.run(main())
