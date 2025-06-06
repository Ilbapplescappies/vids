import asyncio
from bleak import BleakClient, BleakScanner
import struct

# UUID for standard BLE Heart Rate Measurement
HEART_RATE_MEASUREMENT_UUID = "00002A37-0000-1000-8000-00805f9b34fb"

def parse_heart_rate(data: bytearray):
    flags = data[0]
    hr_format = flags & 0x01
    if hr_format == 0:
        return data[1]
    else:
        return struct.unpack("<H", data[1:3])[0]

def handle_heart_rate_notification(sender, data):
    hr = parse_heart_rate(data)
    print(f"❤️ Live Heart Rate: {hr} bpm")

async def main():
    print("🔍 Scanning for Polar Verity Sense...")
    devices = await BleakScanner.discover(timeout=10.0)
    polar_device = next((d for d in devices if d.name and "Polar" in d.name), None)

    if not polar_device:
        print("❌ Polar Verity Sense not found.")
        return

    print(f"✅ Found: {polar_device.name} ({polar_device.address})")

    async with BleakClient(polar_device.address) as client:
        print("🔗 Connecting...")
        try:
            await asyncio.wait_for(client.connect(), timeout=10.0)
            print("✅ Connected!")
        except asyncio.TimeoutError:
            print("❌ Connection timed out.")
            return

        print("📡 Subscribing to heart rate...")
        try:
            await client.start_notify(HEART_RATE_MEASUREMENT_UUID, handle_heart_rate_notification)
            print("⏳ Streaming heart rate... Press Ctrl+C to stop.")
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Stopped by user.")
        finally:
            await client.stop_notify(HEART_RATE_MEASUREMENT_UUID)
            print("📴 Notifications stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"❌ Error: {e}")
