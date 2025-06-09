from bleak import BleakClient
import asyncio

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"  

def handle_hr_data(sender, data):
    bpm = data[1] 
    print(f"Heart rate: {bpm} BPM")

async def main():
    address = "24:AC:AC:04:C1:9D"  
    async with BleakClient(address) as client:
        await client.start_notify(HR_UUID, handle_hr_data)
        print("Subscribed to heart rate notifications.")
        await asyncio.sleep(30)  
        await client.stop_notify(HR_UUID)

asyncio.run(main())

