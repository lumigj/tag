from __future__ import annotations

import argparse
import fcntl
import json
import os
import queue
import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from serial import Serial, SerialException

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pc.locator_core import parse_sample, sample_to_message, select_port


DEFAULT_COMPORT = "/dev/ttyACM0"
DEFAULT_DB_PATH = "hub_u1.db"
COMMIT_INTERVAL = 1.0
THRESHOLD_MS = 1500

BROKER = "broker.emqx.io"
BROKER_PORT = 1883
TOPIC_PREFIX = "/is4151-is5451/tag-locator/v1"
USERNAME = "emqx"
PASSWORD = "public"

RING_SERIAL_LINE = "CMD:RING"


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("Connected to MQTT Broker!")
    else:
        print("Failed to connect, return code {}".format(reason_code))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read mixed serial data and route motion packets to SQLite and locator packets to MQTT."
    )
    parser.add_argument("port_arg", nargs="?", help="Optional serial port, e.g. /dev/ttyACM0")
    parser.add_argument("--port", help="Serial port for the receiver micro:bit, e.g. /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=9600, help="Serial baud rate")
    parser.add_argument("--broker", default=BROKER, help="MQTT broker hostname")
    parser.add_argument("--broker-port", type=int, default=BROKER_PORT, help="MQTT broker port")
    parser.add_argument("--topic-prefix", default=TOPIC_PREFIX, help="MQTT topic prefix")
    parser.add_argument("--tag-id", default="tag-1", help="Tag identifier appended to the MQTT topic")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="SQLite database path for motion packets")
    parser.add_argument(
        "--commit-interval",
        type=float,
        default=COMMIT_INTERVAL,
        help="SQLite commit interval in seconds for motion packets",
    )
    parser.add_argument(
        "--session-threshold-ms",
        type=int,
        default=THRESHOLD_MS,
        help="Gap threshold in milliseconds used to split motion sessions",
    )
    parser.add_argument("--stdin", action="store_true", help="Read raw lines from stdin instead of serial")
    parser.add_argument("--max-messages", type=int, help="Optional number of locator samples to publish")
    return parser


def build_sample_topic(topic_prefix: str, tag_id: str) -> str:
    return "{}/{}/sample".format(topic_prefix.rstrip("/"), tag_id)


def build_cmd_topic(topic_prefix: str, tag_id: str) -> str:
    return "{}/{}/cmd".format(topic_prefix.rstrip("/"), tag_id)


def acquire_port_lock(port: str):
    lock_name = port.strip("/").replace("/", "_") or "serial"
    lock_path = Path("/tmp") / "tag-serial-{}.lock".format(lock_name)
    lock_file = lock_path.open("w")

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise SystemExit(
            "Serial port {} is already being consumed by another tag reader process.".format(port)
        )

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


class MotionRecorder:
    def __init__(self, db_path: str, commit_interval: float, threshold_ms: int):
        self.conn = sqlite3.connect(db_path)
        self.db = self.conn.cursor()
        self.commit_interval = commit_interval
        self.threshold_ms = threshold_ms
        self.last_commit_time = time.monotonic()
        self.last_time = 0
        self.current_session = 0
        self.rows_since_commit = 0
        self.last_row_timestamp: str | None = None

        self.init_db()
        self.get_last_state()

    def init_db(self):
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS accelerometer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                running_time INTEGER NOT NULL,
                strength INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                uploaded INTEGER DEFAULT 0 NOT NULL
               )"""
        )
        self.conn.commit()

    def get_last_state(self):
        self.db.execute("SELECT running_time, session_id FROM accelerometer ORDER BY rowid DESC LIMIT 1")
        result = self.db.fetchone()

        if result:
            self.last_time, self.current_session = result
            print(
                "Recovered motion state: Last Time={}ms, Session={}".format(
                    self.last_time,
                    self.current_session,
                )
            )
        else:
            print("No existing motion data found. Starting fresh at Session 0.")

    def parse_motion_line(self, raw_string: str) -> dict[str, int | str] | None:
        clean_string = raw_string.strip()
        if not clean_string.startswith("A"):
            return None

        parts = clean_string.split()
        if len(parts) != 3:
            print("Skipping malformed motion payload: '{}'".format(clean_string))
            return None

        try:
            return {
                "timestamp": datetime.now().isoformat(),
                "running_time": int(parts[1]),
                "strength": int(parts[2]),
            }
        except ValueError as err:
            print("Error parsing motion payload '{}': {}".format(raw_string, err))
            return None

    def append_with_session(self, data_dict: dict[str, int | str]) -> dict[str, int | str]:
        new_time = int(data_dict["running_time"])

        if self.last_time != 0:
            if (new_time - self.last_time) > self.threshold_ms or new_time < self.last_time:
                self.current_session += 1

        data_dict["session_id"] = self.current_session
        self.last_time = new_time
        return data_dict

    def handle_line(self, line: str) -> bool:
        parsed_data = self.parse_motion_line(line)
        if parsed_data is None:
            return False

        full_row = self.append_with_session(parsed_data)
        self.db.execute(
            "INSERT INTO accelerometer VALUES (NULL, ?, ?, ?, ?, 0)",
            (
                full_row["timestamp"],
                full_row["running_time"],
                full_row["strength"],
                full_row["session_id"],
            ),
        )

        self.rows_since_commit += 1
        self.last_row_timestamp = str(full_row["timestamp"])
        self.maybe_commit()
        return True

    def maybe_commit(self, force: bool = False):
        if self.rows_since_commit == 0:
            return

        current_time = time.monotonic()
        if not force and (current_time - self.last_commit_time) < self.commit_interval:
            return

        self.conn.commit()
        self.last_commit_time = current_time
        display_time = self.last_row_timestamp.split("T")[-1][:8] if self.last_row_timestamp else "unknown"
        if force:
            print("Final motion batch committed to SQLite at {}".format(display_time))
        else:
            print("Motion batch committed to SQLite at {}".format(display_time))
        self.rows_since_commit = 0

    def close(self):
        try:
            self.maybe_commit(force=True)
        finally:
            self.conn.close()


def flush_ring_commands_to_serial(serial_conn: Serial, ring_queue: queue.Queue) -> None:
    while True:
        try:
            ring_queue.get_nowait()
        except queue.Empty:
            break
        try:
            serial_conn.write((RING_SERIAL_LINE + "\n").encode("utf-8"))
            serial_conn.flush()
            print("Wrote {} to receiver serial".format(RING_SERIAL_LINE))
        except Exception as err:
            print("Failed to write ring command to serial: {}".format(err))


class RingCommandSubscriber:
    """Subscribes to MQTT .../cmd and queues ring requests for the main serial loop."""

    def __init__(self, broker: str, broker_port: int, topic_prefix: str, tag_id: str, ring_queue: queue.Queue):
        try:
            import paho.mqtt.client as mqtt
        except ModuleNotFoundError as err:
            raise RuntimeError("paho-mqtt is required for ring command subscription.") from err

        self.ring_queue = ring_queue
        self.cmd_topic = build_cmd_topic(topic_prefix, tag_id)
        self.tag_id = tag_id

        def on_connect(client, userdata, flags, reason_code, properties=None):
            if reason_code == 0:
                client.subscribe(self.cmd_topic)
                print("Ring command MQTT subscribed: {}".format(self.cmd_topic))
            else:
                print("Ring MQTT connect failed, code {}".format(reason_code))

        def on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            if payload.get("tag_id") != self.tag_id:
                return
            if payload.get("type") == "ring":
                self.ring_queue.put("ring")

        client_id = "tag-ring-sub-{}".format(random.randint(0, 9999))
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.username_pw_set(USERNAME, PASSWORD)
        self.client.on_connect = on_connect
        self.client.on_message = on_message
        self.client.connect(broker, broker_port)
        self.client.loop_start()

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
        time.sleep(0.1)


class LocatorPublisher:
    def __init__(self, broker: str, broker_port: int, topic_prefix: str, tag_id: str):
        try:
            import paho.mqtt.client as mqtt
        except ModuleNotFoundError as err:
            raise RuntimeError("paho-mqtt is required for locator publishing.") from err

        self.topic = build_sample_topic(topic_prefix, tag_id)
        self.tag_id = tag_id
        self.published_count = 0

        client_id = "python-mqtt-{}".format(random.randint(0, 10000))
        print("client_id={}".format(client_id))
        print("sample_topic={}".format(self.topic))

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.username_pw_set(USERNAME, PASSWORD)
        self.client.on_connect = on_connect
        self.client.connect(broker, broker_port)
        self.client.loop_start()

    def handle_line(self, line: str) -> bool:
        sample = parse_sample(line)
        if sample is None:
            return False

        print("RX raw: {}".format(line))
        print(
            "Parsed locator sample: mode={} rssi=({}, {}, {})".format(
                sample.mode,
                sample.rssi1,
                sample.rssi2,
                sample.rssi3,
            )
        )

        payload = json.dumps(sample_to_message(sample, self.tag_id), separators=(",", ":"))
        result = self.client.publish(self.topic, payload)

        if result.rc == 0:
            self.published_count += 1
            print("Published sample {} to {}".format(self.published_count, self.topic))
        else:
            print("Failed to publish sample to {}".format(self.topic))

        return True

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
        time.sleep(0.1)


def run() -> int:
    args = build_parser().parse_args()

    serial_conn = None
    port_lock = None
    motion_recorder = None
    locator_publisher = None
    ring_subscriber = None
    ring_queue: queue.Queue = queue.Queue()

    try:
        motion_recorder = MotionRecorder(
            db_path=args.db_path,
            commit_interval=args.commit_interval,
            threshold_ms=args.session_threshold_ms,
        )
        locator_publisher = LocatorPublisher(
            broker=args.broker,
            broker_port=args.broker_port,
            topic_prefix=args.topic_prefix,
            tag_id=args.tag_id,
        )
        if not args.stdin:
            ring_subscriber = RingCommandSubscriber(
                broker=args.broker,
                broker_port=args.broker_port,
                topic_prefix=args.topic_prefix,
                tag_id=args.tag_id,
                ring_queue=ring_queue,
            )

        if args.stdin:
            print("Reading raw lines from stdin... Press CTRL+D to stop.")
        else:
            port = select_port(args.port or args.port_arg or DEFAULT_COMPORT)
            port_lock = acquire_port_lock(port)
            serial_conn = Serial(port, args.baud, timeout=1)
            print("Reading mixed receiver data from {} at {} baud.".format(port, args.baud))

        while True:
            if args.stdin:
                raw_line = sys.stdin.readline()
                if raw_line == "":
                    break
                line = raw_line.strip()
            else:
                flush_ring_commands_to_serial(serial_conn, ring_queue)
                raw = serial_conn.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()

            if not line:
                continue

            prefix = line[:1]
            if prefix == "A":
                if not motion_recorder.handle_line(line):
                    print("Ignored malformed motion payload: {}".format(line))
                continue

            if prefix in {"L", "T"}:
                if not locator_publisher.handle_line(line):
                    print("Ignored malformed locator payload: {}".format(line))
                    continue

                if args.max_messages is not None and locator_publisher.published_count >= args.max_messages:
                    break
                continue

            print("Ignored unsupported payload: {}".format(line))

    except KeyboardInterrupt:
        print("Program terminated!")
    except SerialException as err:
        print("SerialException: {}".format(err))
        return 1
    except Exception as err:
        print("Error occurred: {}".format(err))
        return 1
    finally:
        if serial_conn is not None:
            serial_conn.close()
        if port_lock is not None:
            port_lock.close()
        if motion_recorder is not None:
            motion_recorder.close()
        if ring_subscriber is not None:
            ring_subscriber.close()
        if locator_publisher is not None:
            locator_publisher.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
