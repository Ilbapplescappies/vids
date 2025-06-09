import asyncio
import struct
import threading
from bleak import BleakClient, BleakScanner
from flask import Flask, jsonify

UUID = "00002A37-0000-1000-8000-00805f9b34fb"
app = Flask(__name__)
latest_heart_rate = {"bpm": None}

# BLE loop
async def ble_loop():
    print("Scanning for devices...")
    devices = await BleakScanner.discover(timeout=5.0)

    device = None
    for d in devices:
        if d.name and "Polar" in d.name:
            device = d
            break

    if not device:
        print("Polar device not found.")
        return

    print("Found:", device.name, device.address)

    async with BleakClient(device.address) as client:
        print("Connected.")

        def handle(sender, data):
            if data[0] & 0x01 == 0:
                hr = data[1]
            else:
                hr = struct.unpack("<H", data[1:3])[0]
            latest_heart_rate["bpm"] = hr
            print("Heart Rate:", hr, "bpm")

        await client.start_notify(UUID, handle)

        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            await client.stop_notify(UUID)
            print("BLE loop stopped.")

# Flask API endpoint
@app.route("/heart-rate", methods=["GET"])
def get_heart_rate():
    bpm = latest_heart_rate["bpm"]
    return jsonify({"heart_rate": bpm})

# Start BLE in background
def start_ble():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_loop())

if __name__ == "__main__":
    # Start BLE in a separate thread
    t = threading.Thread(target=start_ble)
    t.daemon = True
    t.start()

    # Run Flask app
    app.run(host="0.0.0.0", port=5000)
