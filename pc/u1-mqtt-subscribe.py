import sqlite3
import random
import time
import pandas as pd
import numpy as np
from datetime import datetime
import json
import joblib
import paho.mqtt.client as mqtt
import threading

WINDOW_SIZE = 7
READ_SIZE = 8
STEP_SIZE = WINDOW_SIZE // 2
DB_R = "cloud_u1raw.db"
DB_F = "cloud_u1fea.db"
CATCH_WINDOW = 0.5

def init_db_r():
    conn = sqlite3.connect(DB_R)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS raw_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        running_time INTEGER NOT NULL,
        strength INTEGER NOT NULL,
        session_id INTEGER NOT NULL
    )
    """)

    conn.commit()
    conn.close()

def init_db_f():
    conn = sqlite3.connect(DB_F)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS processed_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        mean REAL NOT NULL,
        std REAL NOT NULL,
        max REAL NOT NULL,
        min REAL NOT NULL,
        p2p REAL NOT NULL,
        zcr REAL NOT NULL,
        max_abs_diff REAL NOT NULL,
        initial_delta REAL NOT NULL,
        label TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()

def save_rawdata(data):
    conn = sqlite3.connect(DB_R)
    c = conn.cursor()

    c.execute("""
        INSERT INTO raw_data
        (timestamp, running_time, strength, session_id)
        VALUES (?, ?, ?, ?)
    """, (
        data["timestamp"],
        data["running_time"],
        data["strength"],
        data["session_id"]
    ))

    conn.commit()
    conn.close()

def save_processed_data(features, label):
    conn = sqlite3.connect(DB_F)
    c = conn.cursor()

    def to_python_scalar(value):
        if hasattr(value, "item"):
            value = value.item()
        return value

    c.execute("""
        INSERT INTO processed_data (timestamp, mean, std, max, min, p2p, zcr, max_abs_diff, initial_delta, label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        features["timestamp"],
        to_python_scalar(features["mean"]),
        to_python_scalar(features["std"]),
        to_python_scalar(features["max"]),
        to_python_scalar(features["min"]),
        to_python_scalar(features["p2p"]),
        to_python_scalar(features["zcr"]),
        to_python_scalar(features["max_abs_diff"]),
        to_python_scalar(features["initial_delta"]),
        label,
    ))

    conn.commit()
    conn.close()

def get_latest_id():
    conn = sqlite3.connect(DB_R)
    c = conn.cursor()

    c.execute("""
        SELECT id
        FROM raw_data
        ORDER BY id DESC
        LIMIT 1
    """)
    row = c.fetchone()
    conn.close()

    if row is None:
        return 0
    return row[0]

def get_latest_rows(limit=READ_SIZE):
    conn = sqlite3.connect(DB_R)

    query = f"""
        SELECT timestamp, running_time, strength, session_id
        FROM raw_data
        ORDER BY id DESC
        LIMIT {limit}
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        return df
    df = df.iloc[::-1].reset_index(drop=True)
    return df

def extract_features(df):
    """
    FEATURE ENGINEER: Transforms raw accelerometer windows into a statistical fingerprint.

    Input:
        DataFrame with ['running_time', 'strength', 'session_id'] (Raw sensor data at 10Hz)

    Output:
        Dictionary containing:
        - timestamp: The time of processing the window (ms)
        - mean/std: Central tendency and vibration intensity
        - max/min/p2p: Peak force and dynamic range of the movement
        - zcr: Zero Crossing Rate (frequency of oscillation around 1g)
        - max_abs_diff: The 'sharpness' of the movement (detects muted bumps)
        - initial_delta: Directional trend (distinguishes lift vs. fall)
    """

    # 1. Safety Check: Ensure we have enough data
    if len(df) < WINDOW_SIZE:
        return None

    # 2. Get the most recent N samples (the window)
    window = df.iloc[-WINDOW_SIZE:]

    # 3. THE SESSION RULE: Ensure all samples in the window belong to the same session
    # If the window spans two sessions, the features will be corrupted garbage.
    if window['session_id'].nunique() != 1:
        return None

    # 4. Extract and Normalize values (1000 = 1.0g)
    strength_values = window['strength'].values / 1000.0

    # 5. Calculate Features
    features = {
        'timestamp':      datetime.now().isoformat(),
        'mean':           np.mean(strength_values),
        'std':            np.std(strength_values),
        'max':            np.max(strength_values),
        'min':            np.min(strength_values),
        'p2p':            np.ptp(strength_values),
        'zcr':            np.sum(np.diff(strength_values > 1.0) != 0),
        'max_abs_diff':   np.max(np.abs(np.diff(strength_values))),
        'initial_delta':  strength_values[2] - strength_values[0]
    }

    return features

def predict_label(features,bundle):
    loaded_model = bundle['model']
    feature_cols = bundle["feature_cols"]
    x = [[features[col] for col in feature_cols]]
    pred = loaded_model.predict(x)[0]
    return str(pred)

def run_prediction_loop():
    last_seen_raw_id = 0
    bundle = joblib.load("rf_baseline_bundle.pkl")

    while True:
        try:
            current_latest_id = get_latest_id()

            if current_latest_id == 0:
                time.sleep(CATCH_WINDOW)
                continue

            if current_latest_id == last_seen_raw_id:
                time.sleep(CATCH_WINDOW)
                continue

            df = get_latest_rows(limit=READ_SIZE)

            features = extract_features(df)
            if features is None:
                print("Feature extraction skipped: not enough data or mixed session window.")
                last_seen_raw_id = current_latest_id
                time.sleep(CATCH_WINDOW)
                continue
            label = predict_label(features,bundle)
            print(
                f"Predicted for timestamp={features['timestamp']}, label={label}"
            )
            save_processed_data(features, label)

            last_seen_raw_id = current_latest_id
            
        except Exception as e:
            print("Prediction loop error:", e)

        time.sleep(CATCH_WINDOW)


def on_connect(client, userdata, flags, rc):
	
	if rc == 0:
	
		print('Connected to MQTT Broker!')
		
	else:
	
		print('Failed to connect, return code {:d}'.format(rc))



def on_message(client, userdata, msg):
	try:
		data = json.loads(msg.payload.decode("utf-8"))
		save_rawdata(data)         
		print('Received {} from {} topic'.format(msg.payload.decode(), msg.topic))
	except Exception as e:
		print("on_message error:", e)




def run_mqtt():

	broker = 'broker.emqx.io'
	port = 1883
	topic = '/is4151-group04/usecase1'
	client_id = f'pc_u1_receiver_{random.randint(0, 100)}'
	username = 'emqx'
	password = 'public'

	print('client_id={}'.format(client_id))



	# Set Connecting Client ID
	client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id)
	client.username_pw_set(username, password)
	client.on_connect = on_connect
	client.connect(broker, port)
	
	client.subscribe(topic)
	client.on_message = on_message

	client.loop_forever()				


def main():
    init_db_r()
    init_db_f()
    mqtt_thread = threading.Thread(target=run_mqtt, daemon=True)
    pred_thread = threading.Thread(target=run_prediction_loop, daemon=True)

    mqtt_thread.start()
    pred_thread.start()
    print('Program running... Press CTRL+C to exit')
    while True:
        try:                                              
            time.sleep(0.1)         
        except KeyboardInterrupt:                  
            
            print('Program terminating...')
            
            break
        
        except Exception as error:
            
            print('Error: {}'.format(error.args[0]))
    
    print('Program exited...')
if __name__ == '__main__':
    main()
