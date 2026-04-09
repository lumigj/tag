from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from serial import Serial

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pc.locator_core import parse_sample, sample_to_message, select_port


BROKER = "broker.emqx.io"
BROKER_PORT = 1883
TOPIC_PREFIX = "/is4151-is5451/tag-locator/v1"
USERNAME = "emqx"
PASSWORD = "public"


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("Connected to MQTT Broker!")
    else:
        print("Failed to connect, return code {}".format(reason_code))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read receiver serial data and publish parsed locator samples to MQTT."
    )
    parser.add_argument("port_arg", nargs="?", help="Optional serial port, e.g. /dev/ttyACM0")
    parser.add_argument("--port", help="Serial port for the receiver micro:bit, e.g. /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=9600, help="Serial baud rate")
    parser.add_argument("--broker", default=BROKER, help="MQTT broker hostname")
    parser.add_argument("--broker-port", type=int, default=BROKER_PORT, help="MQTT broker port")
    parser.add_argument("--topic-prefix", default=TOPIC_PREFIX, help="MQTT topic prefix")
    parser.add_argument("--tag-id", default="tag-1", help="Tag identifier appended to the MQTT topic")
    parser.add_argument("--stdin", action="store_true", help="Read raw lines from stdin instead of serial")
    parser.add_argument("--max-messages", type=int, help="Optional number of parsed samples to publish before exiting")
    return parser


def build_sample_topic(topic_prefix: str, tag_id: str) -> str:
    return "{}/{}/sample".format(topic_prefix.rstrip("/"), tag_id)


def run() -> int:
    args = build_parser().parse_args()

    topic = build_sample_topic(args.topic_prefix, args.tag_id)
    client_id = "python-mqtt-{}".format(random.randint(0, 10000))
    print("client_id={}".format(client_id))
    print("sample_topic={}".format(topic))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.connect(args.broker, args.broker_port)
    client.loop_start()

    serial_conn = None
    published_count = 0

    try:
        if args.stdin:
            print("Reading raw lines from stdin... Press CTRL+D to stop.")
        else:
            port = select_port(args.port or args.port_arg)
            serial_conn = Serial(port, args.baud, timeout=1)
            print("Reading receiver data from {} at {} baud.".format(port, args.baud))

        while True:
            if args.stdin:
                raw_line = sys.stdin.readline()
                if raw_line == "":
                    break
                line = raw_line.strip()
            else:
                raw = serial_conn.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()

            sample = parse_sample(line)
            if sample is None:
                continue

            payload = json.dumps(sample_to_message(sample, args.tag_id), separators=(",", ":"))
            result = client.publish(topic, payload)
            status = result[0]

            if status == 0:
                published_count += 1
                print("Published sample {} to {}".format(published_count, topic))
            else:
                print("Failed to publish sample to {}".format(topic))

            if args.max_messages is not None and published_count >= args.max_messages:
                break

    except KeyboardInterrupt:
        print("Program terminated!")
    except Exception as err:
        print("Error occurred: {}".format(err))
        return 1
    finally:
        if serial_conn is not None:
            serial_conn.close()
        client.loop_stop()
        client.disconnect()
        time.sleep(0.1)

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
