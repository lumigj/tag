from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import deque
from pathlib import Path
from queue import Empty, SimpleQueue

import matplotlib.pyplot as plt
import paho.mqtt.client as mqtt
from matplotlib.animation import FuncAnimation
from matplotlib.axes import Axes
from matplotlib.collections import PathCollection
from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib.widgets import Button, TextBox

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pc.locator_core import (
    BEACON_ORDER,
    TagSample,
    active_labels_for_mode,
    default_beacon_coords,
    filter_sample,
    layout_center,
    new_filter_states_by_mode,
    proportional_triangle_position,
    sample_from_message,
)


BROKER = "broker.emqx.io"
BROKER_PORT = 1883
TOPIC_PREFIX = "/is4151-is5451/tag-locator/v1"
USERNAME = "emqx"
PASSWORD = "public"


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("Connected to MQTT Broker!")
        client.subscribe(userdata["sample_topic"])
        print("Subscribed to {}".format(userdata["sample_topic"]))
    else:
        print("Failed to connect, return code {}".format(reason_code))


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except json.JSONDecodeError:
        print("Skipping non-JSON payload on {}".format(msg.topic))
        return

    if payload.get("tag_id") != userdata["tag_id"]:
        return

    if payload.get("type") != "tag_sample":
        return

    sample = sample_from_message(payload)
    if sample is None:
        print("Skipping malformed sample payload on {}".format(msg.topic))
        return
    userdata["sample_queue"].put(sample)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Subscribe to MQTT locator samples and use abs(rssi) - offset positioning."
    )
    parser.add_argument("--broker", default=BROKER, help="MQTT broker hostname")
    parser.add_argument("--broker-port", type=int, default=BROKER_PORT, help="MQTT broker port")
    parser.add_argument("--topic-prefix", default=TOPIC_PREFIX, help="MQTT topic prefix")
    parser.add_argument("--tag-id", default="tag-1", help="Tag identifier appended to the MQTT topic")
    parser.add_argument("--history", type=int, default=20, help="Number of past points to keep on screen")
    parser.add_argument("--save", type=Path, help="Optional CSV file path to append raw samples while plotting")
    parser.add_argument("--headless", action="store_true", help="Consume MQTT samples and print estimates without opening the plot window")
    parser.add_argument("--duration", type=float, default=8.0, help="Headless runtime in seconds")
    parser.add_argument("--offset", type=float, default=0.0, help="Initial abs(rssi) offset to subtract before solving")
    return parser


def build_sample_topic(topic_prefix: str, tag_id: str) -> str:
    return "{}/{}/sample".format(topic_prefix.rstrip("/"), tag_id)


def update_layout(
    mode: int,
    beacon_coords: dict[str, tuple[float, float]],
    line_artists: list[Line2D],
    beacon_scatter: PathCollection,
    beacon_labels: dict[str, Text],
    mode_text: Text,
    ax: Axes,
) -> None:
    for line in line_artists:
        line.set_data([], [])

    beacon_scatter.set_offsets([beacon_coords[label] for label in BEACON_ORDER])
    beacon_scatter.set_sizes([300] * len(BEACON_ORDER))

    if mode == 3:
        line_artists[0].set_data(
            [beacon_coords["B1"][0], beacon_coords["B2"][0]],
            [beacon_coords["B1"][1], beacon_coords["B2"][1]],
        )
        line_artists[1].set_data(
            [beacon_coords["B1"][0], beacon_coords["B3"][0]],
            [beacon_coords["B1"][1], beacon_coords["B3"][1]],
        )
        line_artists[2].set_data(
            [beacon_coords["B2"][0], beacon_coords["B3"][0]],
            [beacon_coords["B2"][1], beacon_coords["B3"][1]],
        )
        beacon_scatter.set_color(["#1d3557", "#1d3557", "#1d3557"])
        mode_text.set_text("TRIANGLE MODE: OFFSET RAW")
        mode_text.set_color("#2a9d8f")
        ax.set_title("Distance proxy = max(abs(rssi) - offset, 0)", pad=18)
    else:
        line_artists[0].set_data(
            [beacon_coords["B1"][0], beacon_coords["B2"][0]],
            [beacon_coords["B1"][1], beacon_coords["B2"][1]],
        )
        beacon_scatter.set_color(["#1d3557", "#1d3557", "#cbd5e1"])
        mode_text.set_text("LINE MODE: OFFSET RAW")
        mode_text.set_color("#457b9d")
        ax.set_title("Distance proxy = max(abs(rssi) - offset, 0)", pad=18)

    for label, text in beacon_labels.items():
        x, y = beacon_coords[label]
        if y >= 0.75:
            text.set_position((x, y - 0.09))
            text.set_va("top")
        else:
            text.set_position((x, y + 0.07))
            text.set_va("bottom")
        text.set_color("#8a8f98" if mode == 2 and label == "B3" else "black")
        text.set_visible(True)


def consume_incoming_messages(
    sample_queue: SimpleQueue[TagSample],
    history: deque[TagSample],
    latest_sample_box: dict[str, TagSample | None],
    filter_states_by_mode: dict[int, dict[str, object]],
    csv_file,
) -> None:
    while True:
        try:
            raw_sample = sample_queue.get_nowait()
        except Empty:
            break

        sample = filter_sample(raw_sample, filter_states_by_mode[raw_sample.mode])
        history.append(sample)
        latest_sample_box["sample"] = sample

        if csv_file is not None:
            csv_file.write(
                "{},{},{},{},{}\n".format(
                    raw_sample.timestamp,
                    raw_sample.mode,
                    raw_sample.rssi1,
                    raw_sample.rssi2,
                    raw_sample.rssi3,
                )
            )
            csv_file.flush()


def offset_distance_proxy(rssi: int, offset: float) -> float:
    proxy = abs(rssi) - offset
    if proxy < 0.0:
        return 0.0
    return proxy


def estimate_offset_position(
    sample: TagSample,
    beacon_coords: dict[str, tuple[float, float]],
    offset: float,
) -> tuple[float, float, str]:
    if sample.mode == 3 and sample.rssi3 is not None:
        distances = {
            "B1": offset_distance_proxy(sample.rssi1, offset),
            "B2": offset_distance_proxy(sample.rssi2, offset),
            "B3": offset_distance_proxy(sample.rssi3, offset),
        }
        position = proportional_triangle_position(beacon_coords, distances)
        return position[0], position[1], "OFF-B123"

    distance1 = offset_distance_proxy(sample.rssi1, offset)
    distance2 = offset_distance_proxy(sample.rssi2, offset)
    total = distance1 + distance2
    if total <= 0:
        center = layout_center(beacon_coords, 2)
        return center[0], center[1], "OFF-B12"

    ratio_from_b1 = distance1 / total
    x = beacon_coords["B1"][0] + (beacon_coords["B2"][0] - beacon_coords["B1"][0]) * ratio_from_b1
    y = beacon_coords["B1"][1] + (beacon_coords["B2"][1] - beacon_coords["B1"][1]) * ratio_from_b1
    return round(x, 4), round(y, 4), "OFF-B12"


def run() -> int:
    args = build_parser().parse_args()

    sample_topic = build_sample_topic(args.topic_prefix, args.tag_id)
    sample_queue: SimpleQueue[TagSample] = SimpleQueue()

    client_id = "python-mqtt-{}".format(random.randint(0, 10000))
    print("client_id={}".format(client_id))
    print("sample_topic={}".format(sample_topic))

    userdata = {
        "tag_id": args.tag_id,
        "sample_topic": sample_topic,
        "sample_queue": sample_queue,
    }

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.user_data_set(userdata)
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.broker_port)
    client.loop_start()

    history: deque[TagSample] = deque(maxlen=args.history)
    latest_sample_box: dict[str, TagSample | None] = {"sample": None}
    filter_states_by_mode = new_filter_states_by_mode()
    beacon_coords = default_beacon_coords()
    offset_box = {"value": float(args.offset)}

    csv_file = None
    if args.save:
        csv_file = args.save.open("a", encoding="utf-8")
        if args.save.stat().st_size == 0:
            csv_file.write("timestamp,mode,rssi1,rssi2,rssi3\n")
            csv_file.flush()

    try:
        if args.headless:
            print("Running offset subscriber for {:.1f} seconds with offset={}...".format(args.duration, offset_box["value"]))
            deadline = time.time() + args.duration
            last_reported = 0

            while time.time() < deadline:
                consume_incoming_messages(
                    sample_queue=sample_queue,
                    history=history,
                    latest_sample_box=latest_sample_box,
                    filter_states_by_mode=filter_states_by_mode,
                    csv_file=csv_file,
                )

                if history:
                    latest_sample = history[-1]
                    latest_key = int(latest_sample.timestamp * 1000)
                    if latest_key != last_reported:
                        estimate = estimate_offset_position(latest_sample, beacon_coords, offset_box["value"])
                        print(
                            "mode={} estimate={} x={} y={} offset={} rssi=({}, {}, {})".format(
                                latest_sample.mode,
                                estimate[2],
                                estimate[0],
                                estimate[1],
                                offset_box["value"],
                                latest_sample.rssi1,
                                latest_sample.rssi2,
                                latest_sample.rssi3,
                            )
                        )
                        last_reported = latest_key

                time.sleep(0.05)

            return 0

        fig, ax = plt.subplots(figsize=(19.5, 13.8))
        fig.subplots_adjust(left=0.14, right=0.98, bottom=0.30, top=0.92)
        fig.canvas.manager.set_window_title("micro:bit locator (MQTT, offset raw)")
        ax.set_xlim(0.09, 0.91)
        ax.set_ylim(0.07, 0.82)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_aspect("equal")

        line_artists = [
            ax.plot([], [], color="#264653", linewidth=3, zorder=1)[0],
            ax.plot([], [], color="#264653", linewidth=3, zorder=1)[0],
            ax.plot([], [], color="#264653", linewidth=3, zorder=1)[0],
        ]
        beacon_scatter = ax.scatter([], [], s=[], c=[], marker="s", zorder=3)
        beacon_labels = {
            "B1": ax.text(0, 0, "B1", ha="center", va="bottom", fontsize=11, weight="bold"),
            "B2": ax.text(0, 0, "B2", ha="center", va="bottom", fontsize=11, weight="bold"),
            "B3": ax.text(0, 0, "B3", ha="center", va="bottom", fontsize=11, weight="bold"),
        }
        trail_scatter = ax.scatter([], [], s=[], c=[], alpha=0.35, zorder=2)
        initial_center = layout_center(beacon_coords, 3)
        current_scatter = ax.scatter(
            [initial_center[0]],
            [initial_center[1]],
            s=[280],
            c=["#adb5bd"],
            edgecolors="black",
            zorder=4,
        )
        info_text = fig.text(
            0.07,
            0.81,
            "Waiting for MQTT tag data...",
            ha="left",
            va="top",
            fontsize=9,
            family="monospace",
            linespacing=1.35,
            bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": "#adb5bd"},
        )
        mode_text = fig.text(
            0.5,
            0.95,
            "",
            ha="center",
            va="center",
            fontsize=12,
            weight="bold",
            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#cbd5e1"},
        )

        fig.text(0.07, 0.23, "Offset Method", ha="left", va="center", fontsize=11, weight="bold")
        fig.text(
            0.07,
            0.205,
            "Distance proxy = max(abs(rssi) - offset, 0). Enter offset and click Apply Offset.",
            ha="left",
            va="center",
            fontsize=9,
            color="#6b7280",
        )
        offset_input = TextBox(fig.add_axes([0.07, 0.11, 0.12, 0.06]), "", initial=str(args.offset))
        apply_offset_button = Button(fig.add_axes([0.21, 0.11, 0.16, 0.06]), "Apply Offset")
        offset_status_text = fig.text(
            0.42,
            0.20,
            "",
            ha="left",
            va="top",
            fontsize=9,
            family="monospace",
            linespacing=1.35,
            bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": "#adb5bd"},
        )
        fig.text(
            0.07,
            0.03,
            "This entrypoint does not use calibration. Use pc.subscriber for the overlay view.",
            ha="left",
            va="center",
            fontsize=9,
            color="#6b7280",
        )

        def refresh_offset_status() -> None:
            offset_status_text.set_text(
                "offset = {}\nproxy example:\n-21 -> {}\n-50 -> {}".format(
                    offset_box["value"],
                    int(offset_distance_proxy(-21, offset_box["value"])),
                    int(offset_distance_proxy(-50, offset_box["value"])),
                )
            )

        def apply_offset(_event=None) -> None:
            raw_value = offset_input.text.strip()
            try:
                offset_box["value"] = float(raw_value)
            except ValueError:
                print("Offset must be a number.")
                return
            refresh_offset_status()
            fig.canvas.draw_idle()
            print("Applied offset {}.".format(offset_box["value"]))

        apply_offset_button.on_clicked(apply_offset)

        current_mode = 2
        update_layout(
            current_mode,
            beacon_coords,
            line_artists,
            beacon_scatter,
            beacon_labels,
            mode_text,
            ax,
        )
        refresh_offset_status()

        def update(_frame: int):
            nonlocal current_mode

            consume_incoming_messages(
                sample_queue=sample_queue,
                history=history,
                latest_sample_box=latest_sample_box,
                filter_states_by_mode=filter_states_by_mode,
                csv_file=csv_file,
            )

            if not history:
                return current_scatter, trail_scatter, info_text

            latest = history[-1]
            if latest.mode != current_mode:
                current_mode = latest.mode
                update_layout(
                    current_mode,
                    beacon_coords,
                    line_artists,
                    beacon_scatter,
                    beacon_labels,
                    mode_text,
                    ax,
                )

            positions = [
                estimate_offset_position(sample, beacon_coords, offset_box["value"])
                for sample in history
                if sample.mode == current_mode
            ]
            mode_history = [sample for sample in history if sample.mode == current_mode]
            if not positions or not mode_history:
                return current_scatter, trail_scatter, info_text

            latest_position = positions[-1]
            latest_sample = mode_history[-1]

            if len(positions) > 1:
                trail_scatter.set_offsets([[point[0], point[1]] for point in positions[:-1]])
                trail_scatter.set_sizes([60 + index * 6 for index, _ in enumerate(positions[:-1])])
                trail_scatter.set_color(["#94a3b8"] * len(positions[:-1]))
            else:
                trail_scatter.set_offsets([[-1, -1]])
                trail_scatter.set_sizes([0])
                trail_scatter.set_color(["#ffffff"])

            current_scatter.set_offsets([[latest_position[0], latest_position[1]]])
            current_scatter.set_sizes([300])
            current_scatter.set_color(["#e76f51"])

            active_layout = " ".join(active_labels_for_mode(current_mode))
            info_text.set_text(
                "tag mode = {:>8}\n"
                "estimate = {:>8}\n"
                "rssi B1  = {:>5}\n"
                "rssi B2  = {:>5}\n"
                "rssi B3  = {:>5}\n"
                "offset   = {:>8}\n"
                "layout   = {:>8}".format(
                    "TRIANGLE" if current_mode == 3 else "LINE",
                    latest_position[2],
                    latest_sample.rssi1,
                    latest_sample.rssi2,
                    "--" if latest_sample.rssi3 is None else latest_sample.rssi3,
                    offset_box["value"],
                    active_layout,
                )
            )
            return current_scatter, trail_scatter, info_text

        animation = FuncAnimation(fig, update, interval=80, cache_frame_data=False)
        try:
            plt.show()
        finally:
            animation.event_source.stop()

        return 0

    except KeyboardInterrupt:
        print("Program terminated!")
        return 0
    except Exception as err:
        print("Error occurred: {}".format(err))
        return 1
    finally:
        if csv_file is not None:
            csv_file.close()
        client.loop_stop()
        client.disconnect()
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(run())
