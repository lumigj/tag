import sqlite3
import serial
from datetime import datetime
import time

COMPORT = '/dev/ttyACM0'
WINDOW_SIZE = 7
STEP_SIZE = WINDOW_SIZE // 2
COMMIT_INTERVAL = 1.0
THRESHOLD_MS = 1500

# Initialize Connection globally
conn = sqlite3.connect('hub_u1.db', check_same_thread=False)
db = conn.cursor()

last_commit_time = time.time()
last_time = 0
current_session = 0
counter = 0


def init_db():
    db.execute('''CREATE TABLE IF NOT EXISTS accelerometer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                running_time INTEGER NOT NULL,
                strength INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                uploaded INTEGER DEFAULT 0 NOT NULL
               )''')
    conn.commit()
    
def get_last_state():
    """Retrieves the final timestamp and session from the database to ensure continuity."""
    global last_time, current_session
    
    db.execute("SELECT running_time, session_id FROM accelerometer ORDER BY rowid DESC LIMIT 1")
    result = db.fetchone()
    
    if result:
        last_time, current_session = result
        print(f"Recovered state: Last Time={last_time}ms, Session={current_session}")
    else:
        print("No existing data found. Starting fresh at Session 0.")

def parse_radio_data(raw_string):
    """
    DATA PARSER: Decodes raw radio strings into a structured format.

    Format Change:
    "A 54302 1024"  --->  {'running_time': 54302, 'strength': 1024}

    Returns:
    A dictionary suitable for appending as a row to the main DataFrame.
    """
    try:
        # 1. Clean and check the prefix
        clean_string = raw_string.strip()
        if not clean_string.startswith("A"):
            return None

        # 2. Split and validate length
        parts = clean_string.split()
        if len(parts) != 3:
            print(f"Skipping malformed string: '{clean_string}'")
            return None

        # 3. Convert types and return dict
        return {
            'timestamp': datetime.now().isoformat(),
            'running_time': int(parts[1]),
            'strength': int(parts[2])
        }

    except (ValueError, IndexError) as e:
        # Catches cases where strings aren't numbers or parts are missing
        print(f"Error parsing data '{raw_string}': {e}")
        return None
    

def append_with_session(data_dict):
    """ 
    METADATA ENRICHER: Manages session IDs based on time leaps.

    Logic:
    - Increments 'session_id' if a time gap exceeds THRESHOLD_MS (Signal Loss).
    - Increments 'session_id' if New Time < Last Time (Micro:bit Reset).
    - Updates the global state to track the timeline across calls.

    Returns:
        dict: The original data dictionary updated with a 'session_id' key.
    """

    global last_time, current_session

    new_time = data_dict['running_time']
    
    # Check for gaps or resets
    if last_time != 0: # Skip the very first packet
        if (new_time - last_time) > THRESHOLD_MS or new_time < last_time:
            current_session += 1
            
    data_dict['session_id'] = current_session

    last_time = new_time
    
    return data_dict

# --- Setup ---
init_db()
get_last_state()

try:
    comPort = COMPORT

    ser = serial.Serial(port=comPort, baudrate=9600, timeout=0.1)

    print('Listening on {}... Press CTRL+C to exit'.format(comPort))

    while True:
        # 1. Read from Serial
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8').strip()
            
            # 2. Parse data
            parsed_data = parse_radio_data(line)

            if parsed_data:
                full_row = append_with_session(parsed_data)
                # counter += 1
                        
                # 3. Fast SQLite Insert
                db.execute("INSERT INTO accelerometer VALUES (NULL, ?, ?, ?, ?, 0)",
                            (full_row['timestamp'], full_row['running_time'], 
                            full_row['strength'], full_row['session_id']))
            
                # 4. Commit every COMMIT_INTERVAL seconds
                current_time = time.time()
                if current_time - last_commit_time >= COMMIT_INTERVAL:
                     conn.commit()
                     display_time = full_row['timestamp'].split('T')[-1][:8]
                     print(f"Batch committed to SQLite at {display_time}")
                
                

except serial.SerialException as err:
	
	print('SerialException: {}'.format(err))
    
except KeyboardInterrupt:
    conn.commit() # Save the final bit of data
    
    if counter > 0:
        final_time = full_row['timestamp'].split('T')[-1][:8]
        print(f"Last batch committed to SQLite at {final_time}")
    else:
        print("No data was collected.")

    conn.close()
    print("Cleanly stopped.")
