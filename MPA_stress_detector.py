from bleak import BleakClient, BleakScanner
import asyncio
import pandas as pd
import numpy as np
import json
from datetime import datetime
import warnings
import httpx
from collections import deque

warnings.filterwarnings("ignore")

# BLE UUID for Heart Rate Measurement
HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# Global variables
hr_data_buffer = deque(maxlen=30)  # Keep only the last 30 readings
device_address = "24:AC:AC:04:C1:9D"  # Automatically connect to this device
last_data_time = datetime.now()  # for watchdog

# BLE notification handler
def handle_hr_data(sender, data):
    global last_data_time
    try:
        flags = data[0]
        hr_format = flags & 0x01
        bpm = data[1] if hr_format == 0 else int.from_bytes(data[1:3], byteorder='little')
        if bpm == 0 or bpm < 40 or bpm > 220:
            print("Invalid HR value — skipping.")
            return
        timestamp = datetime.now()
        hr_data_buffer.append((timestamp, bpm))
        last_data_time = timestamp
        print(f"Heart rate: {bpm} BPM")
    except Exception as e:
        print(f"HR data error: {e}")

def build_dataframe(buffer):
    rows = [{'timestamp': ts, 'HR (bpm)': bpm} for ts, bpm in buffer]
    df = pd.DataFrame(rows)
    df.set_index('timestamp', inplace=True)
    return df

def remove_outliers_and_interpolate(df, column, low_rri=300, high_rri=2000):
    df[column] = df[column].mask((df[column] < low_rri) | (df[column] > high_rri), np.nan)
    df[column] = df[column].interpolate(method='linear')
    return df

def calculate_rmssd(rr_intervals):
    diff = np.diff(rr_intervals)
    squared_diff = np.square(diff)
    mean_squared_diff = np.mean(squared_diff)
    return np.sqrt(mean_squared_diff)

def compute_rmssd_series(rr_series, window_size=15, step_size=5):
    rmssd_values = []
    for start in range(0, len(rr_series) - window_size + 1, step_size):
        end = start + window_size
        window = rr_series.iloc[start:end].dropna()
        if len(window) >= 5:
            rmssd = calculate_rmssd(window)
            rmssd_values.extend([rmssd] * step_size)
    while len(rmssd_values) < len(rr_series):
        rmssd_values.append(rmssd_values[-1] if rmssd_values else np.nan)
    return rmssd_values[:len(rr_series)]

def classify_stress_hierarchy(rmssd_value, mean_rmssd, std_rmssd):
    if std_rmssd < 0.001:
        return "Stable"
    if rmssd_value <= mean_rmssd - 0.7 * std_rmssd:
        return "Stressed"
    elif rmssd_value <= mean_rmssd - 0.5 * std_rmssd:
        return "Aroused"
    elif rmssd_value <= mean_rmssd + 0.25 * std_rmssd:
        return "Stable"
    else:
        return "Relaxed"

def process_stress_from_dataframe(df):
    resampled = df.resample('1S').mean().ffill()
    resampled['HR_interp'] = resampled['HR (bpm)'].interpolate(method='linear')
    resampled['RR_intervals'] = 60000 / resampled['HR_interp']
    resampled = remove_outliers_and_interpolate(resampled, 'RR_intervals')
    rmssd_values = compute_rmssd_series(resampled['RR_intervals'])
    resampled['RMSSD'] = rmssd_values
    valid_rmssd = resampled['RMSSD'].dropna()

    if len(valid_rmssd) < 5:
        return {"status": "error", "message": f"Not enough valid RMSSD data (found {len(valid_rmssd)})"}

    rmssd_mean = np.nanmean(valid_rmssd)
    rmssd_std = np.nanstd(valid_rmssd)

    print(f"Debug: HR range={df['HR (bpm)'].max() - df['HR (bpm)'].min()}, RR range={resampled['RR_intervals'].max() - resampled['RR_intervals'].min():.1f}ms")
    print(f"Debug: RMSSD mean={rmssd_mean:.2f}, std={rmssd_std:.2f}, n_values={len(valid_rmssd)}")
    print(f"Debug: RMSSD range={valid_rmssd.min():.2f}-{valid_rmssd.max():.2f}")

    last_window = valid_rmssd.iloc[-15:] if len(valid_rmssd) >= 15 else valid_rmssd
    classifications = last_window.apply(lambda x: classify_stress_hierarchy(x, rmssd_mean, rmssd_std))
    print(f"Debug: Classifications={classifications.value_counts().to_dict()}")

    if 'Stressed' in classifications.values:
        status = 'Stressed'
    elif 'Aroused' in classifications.values:
        status = 'Aroused'
    elif 'Stable' in classifications.values:
        status = 'Stable'
    else:
        status = 'Relaxed'

    avg_hr = df['HR (bpm)'].mean()
    return {
        "id": str(device_address),
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "stress_flag": str(status),
        "heart_rate": int(round(avg_hr))
    }

async def post_result(result):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://10.180.46.211:8776/stress/save",
                json=result,
                timeout=5.0
            )
            if response.status_code == 200:
                print("Sent to external server.")
            else:
                print(f"Server responded with {response.status_code}: {response.text}")
    except httpx.RequestError as e:
        print(f"HTTP request failed: {e}")

async def ble_worker():
    global hr_data_buffer, last_data_time
    last_post_time = datetime.now()

    while True:
        try:
            print(f"Connecting to {device_address} ...")
            async with BleakClient(device_address) as client:
                await client.start_notify(HR_UUID, handle_hr_data)
                print("Connected and receiving data...")
                heartbeat_counter = 0

                while True:
                    await asyncio.sleep(1)
                    heartbeat_counter += 1

                    if heartbeat_counter % 10 == 0:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Health check OK")

                    if (datetime.now() - last_data_time).total_seconds() > 15:
                        print("No data received in 15 seconds. Reconnecting...")
                        raise Exception("Stale HR data")

                    if (datetime.now() - last_post_time).total_seconds() >= 30:
                        if len(hr_data_buffer) >= 5:
                            buffer_list = list(hr_data_buffer)
                            df = build_dataframe(buffer_list)
                            result = process_stress_from_dataframe(df)
                            print(json.dumps(result))
                            await post_result(result)
                        else:
                            print("Not enough HR data to process.")
                        last_post_time = datetime.now()

        except Exception as e:
            print(f"BLE connection error: {e}")
            with open("hr_error_log.txt", "a") as f:
                f.write(f"{datetime.now()} - {str(e)}\n")
            print("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

async def main():
    await ble_worker()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Fatal error: {e}")
        with open("hr_error_log.txt", "a") as f:
            f.write(f"{datetime.now()} - FATAL: {str(e)}\n")
