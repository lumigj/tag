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
    BEACON_COORDS,
    BEACON_ORDER,
    TRIANGLE_BASE_Y,
    TRIANGLE_HEIGHT,
    CalibrationPoint,
    TagSample,
    active_labels_for_mode,
    build_manual_point,
    calibration_entry_text,
    calibration_rows_summary,
    calibration_state_from_message,
    calibration_state_to_message,
    capture_point,
    empty_calibration_state,
    estimate_position,
    filter_sample,
    new_filter_states_by_mode,
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
        client.subscribe(userdata["calibration_topic"])
        print("Subscribed to {}".format(userdata["sample_topic"]))
        print("Subscribed to {}".format(userdata["calibration_topic"]))
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

    message_type = payload.get("type")
    if message_type == "tag_sample":
        sample = sample_from_message(payload)
        if sample is None:
            print("Skipping malformed sample payload on {}".format(msg.topic))
            return
        userdata["sample_queue"].put(sample)
        return

    if message_type == "calibration_state":
        calibration_by_mode = calibration_state_from_message(payload)
        if calibration_by_mode is None:
            print("Skipping malformed calibration payload on {}".format(msg.topic))
            return
        userdata["calibration_queue"].put(calibration_by_mode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Subscribe to MQTT locator samples, estimate position, and render the live plot."
    )
    parser.add_argument("--broker", default=BROKER, help="MQTT broker hostname")
    parser.add_argument("--broker-port", type=int, default=BROKER_PORT, help="MQTT broker port")
    parser.add_argument("--topic-prefix", default=TOPIC_PREFIX, help="MQTT topic prefix")
    parser.add_argument("--tag-id", default="tag-1", help="Tag identifier appended to the MQTT topic")
    parser.add_argument("--history", type=int, default=20, help="Number of past points to keep on screen")
    parser.add_argument(
        "--save",
        type=Path,
        help="Optional CSV file path to append raw samples while plotting",
    )
    parser.add_argument("--headless", action="store_true", help="Consume MQTT samples and print estimates without opening the plot window")
    parser.add_argument("--duration", type=float, default=8.0, help="Headless runtime in seconds")
    return parser


def build_sample_topic(topic_prefix: str, tag_id: str) -> str:
    return "{}/{}/sample".format(topic_prefix.rstrip("/"), tag_id)


def build_calibration_topic(topic_prefix: str, tag_id: str) -> str:
    return "{}/{}/calibration".format(topic_prefix.rstrip("/"), tag_id)


def update_layout(
    mode: int,
    line_artists: list[Line2D],
    beacon_scatter: PathCollection,
    beacon_labels: dict[str, Text],
    mode_text: Text,
    capture_buttons: dict[str, Button],
    matrix_boxes: dict[str, dict[str, TextBox]],
    matrix_row_labels: dict[str, Text],
    matrix_col_labels: dict[str, Text],
    ax: Axes,
) -> None:
    for line in line_artists:
        line.set_data([], [])

    coords = BEACON_COORDS
    beacon_scatter.set_offsets([coords[label] for label in BEACON_ORDER])
    beacon_scatter.set_sizes([300] * len(BEACON_ORDER))

    if mode == 3:
        line_artists[0].set_data([coords["B1"][0], coords["B2"][0]], [coords["B1"][1], coords["B2"][1]])
        line_artists[1].set_data([coords["B1"][0], coords["B3"][0]], [coords["B1"][1], coords["B3"][1]])
        line_artists[2].set_data([coords["B2"][0], coords["B3"][0]], [coords["B2"][1], coords["B3"][1]])
        beacon_scatter.set_color(["#1d3557", "#1d3557", "#1d3557"])
        mode_text.set_text("TRIANGLE MODE: B1-B2-B3")
        mode_text.set_color("#2a9d8f")
        ax.set_title("Triangle mode uses B1, B2, and B3", pad=18)
    else:
        line_artists[0].set_data([coords["B1"][0], coords["B2"][0]], [coords["B1"][1], coords["B2"][1]])
        beacon_scatter.set_color(["#1d3557", "#1d3557", "#cbd5e1"])
        mode_text.set_text("LINE MODE: B1-B2 ONLY")
        mode_text.set_color("#457b9d")
        ax.set_title("Line mode uses B1 and B2; B3 is shown for reference", pad=18)

    for label, text in beacon_labels.items():
        x, y = coords[label]
        if y >= 0.75:
            text.set_position((x, y - 0.09))
            text.set_va("top")
        else:
            text.set_position((x, y + 0.07))
            text.set_va("bottom")
        if mode == 2 and label == "B3":
            text.set_color("#8a8f98")
        else:
            text.set_color("black")
        text.set_visible(True)

    active_labels = set(active_labels_for_mode(mode))
    for label, text in matrix_row_labels.items():
        text.set_color("black" if label in active_labels else "#8a8f98")
        capture_buttons[label].ax.set_facecolor("#d9f2ec" if label in active_labels else "#e5e7eb")
        capture_buttons[label].label.set_color("black" if label in active_labels else "#8a8f98")

    for label, text in matrix_col_labels.items():
        text.set_color("black" if label in active_labels else "#8a8f98")

    for row_label, row_boxes in matrix_boxes.items():
        for col_label, box in row_boxes.items():
            active = row_label in active_labels and col_label in active_labels
            box.ax.set_facecolor("white" if active else "#f3f4f6")
            box.text_disp.set_color("black" if active else "#8a8f98")


def consume_incoming_messages(
    sample_queue: SimpleQueue[TagSample],
    calibration_queue: SimpleQueue[dict[int, dict[str, CalibrationPoint | None]]],
    history: deque[TagSample],
    latest_sample_box: dict[str, TagSample | None],
    calibration_by_mode: dict[int, dict[str, CalibrationPoint | None]],
    filter_states_by_mode: dict[int, dict[str, object]],
    csv_file,
) -> bool:
    calibration_changed = False

    while True:
        try:
            incoming_calibration = calibration_queue.get_nowait()
        except Empty:
            break

        for mode in (2, 3):
            calibration_by_mode[mode] = incoming_calibration[mode]
        calibration_changed = True

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

    return calibration_changed


def run() -> int:
    args = build_parser().parse_args()

    sample_topic = build_sample_topic(args.topic_prefix, args.tag_id)
    calibration_topic = build_calibration_topic(args.topic_prefix, args.tag_id)
    sample_queue: SimpleQueue[TagSample] = SimpleQueue()
    calibration_queue: SimpleQueue[dict[int, dict[str, CalibrationPoint | None]]] = SimpleQueue()

    client_id = "python-mqtt-{}".format(random.randint(0, 10000))
    print("client_id={}".format(client_id))
    print("sample_topic={}".format(sample_topic))
    print("calibration_topic={}".format(calibration_topic))

    userdata = {
        "tag_id": args.tag_id,
        "sample_topic": sample_topic,
        "calibration_topic": calibration_topic,
        "sample_queue": sample_queue,
        "calibration_queue": calibration_queue,
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
    calibration_by_mode = empty_calibration_state()
    filter_states_by_mode = new_filter_states_by_mode()

    csv_file = None
    if args.save:
        csv_file = args.save.open("a", encoding="utf-8")
        if args.save.stat().st_size == 0:
            csv_file.write("timestamp,mode,rssi1,rssi2,rssi3\n")
            csv_file.flush()

    try:
        if args.headless:
            print("Running headless subscriber for {:.1f} seconds...".format(args.duration))
            deadline = time.time() + args.duration
            last_reported = 0

            while time.time() < deadline:
                calibration_changed = consume_incoming_messages(
                    sample_queue=sample_queue,
                    calibration_queue=calibration_queue,
                    history=history,
                    latest_sample_box=latest_sample_box,
                    calibration_by_mode=calibration_by_mode,
                    filter_states_by_mode=filter_states_by_mode,
                    csv_file=csv_file,
                )

                if calibration_changed:
                    print("Calibration state updated from MQTT.")

                if history:
                    latest_sample = history[-1]
                    latest_key = int(latest_sample.timestamp * 1000)
                    if latest_key != last_reported:
                        estimate = estimate_position(latest_sample, calibration_by_mode[latest_sample.mode])
                        print(
                            "mode={} estimate={} x={} y={} rssi=({}, {}, {})".format(
                                latest_sample.mode,
                                estimate[2],
                                estimate[0],
                                estimate[1],
                                latest_sample.rssi1,
                                latest_sample.rssi2,
                                latest_sample.rssi3,
                            )
                        )
                        last_reported = latest_key

                time.sleep(0.05)

            return 0

        fig, ax = plt.subplots(figsize=(11.6, 7.8))
        fig.subplots_adjust(left=0.26, right=0.94, bottom=0.46, top=0.82)
        fig.canvas.manager.set_window_title("micro:bit locator (MQTT)")
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
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
        current_scatter = ax.scatter(
            [0.5],
            [TRIANGLE_BASE_Y + TRIANGLE_HEIGHT / 3],
            s=[280],
            c=["#adb5bd"],
            edgecolors="black",
            zorder=4,
        )
        info_text = fig.text(
            0.08,
            0.76,
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
            0.875,
            "",
            ha="center",
            va="center",
            fontsize=12,
            weight="bold",
            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#cbd5e1"},
        )

        fig.text(0.08, 0.34, "Calibration Matrix", ha="left", va="center", fontsize=11, weight="bold")
        fig.text(
            0.08,
            0.315,
            "Rows are near positions. Columns are the RSSI heard from each beacon.",
            ha="left",
            va="center",
            fontsize=9,
            color="#6b7280",
        )

        matrix_col_positions = {"B1": 0.34, "B2": 0.47, "B3": 0.60}
        matrix_row_positions = {"B1": 0.19, "B2": 0.115, "B3": 0.04}
        matrix_col_labels = {
            label: fig.text(x + 0.05, 0.275, label, ha="center", va="center", fontsize=10, weight="bold")
            for label, x in matrix_col_positions.items()
        }
        matrix_row_labels = {
            label: fig.text(0.08, y + 0.0275, "Near {}".format(label), ha="left", va="center", fontsize=10, weight="bold")
            for label, y in matrix_row_positions.items()
        }
        capture_buttons = {
            label: Button(fig.add_axes([0.17, y, 0.12, 0.055]), "Cap {}".format(label))
            for label, y in matrix_row_positions.items()
        }
        matrix_boxes: dict[str, dict[str, TextBox]] = {}
        for row_label, y in matrix_row_positions.items():
            matrix_boxes[row_label] = {}
            for col_label, x in matrix_col_positions.items():
                matrix_boxes[row_label][col_label] = TextBox(fig.add_axes([x, y, 0.10, 0.055]), "")
        apply_manual_button = Button(fig.add_axes([0.77, 0.11, 0.15, 0.09]), "Apply Matrix")
        fig.text(
            0.08,
            0.015,
            "Manual apply updates only rows where all active cells are filled. Leave a whole row blank to keep it unchanged.",
            ha="left",
            va="center",
            fontsize=9,
            color="#6b7280",
        )

        def refresh_matrix_inputs(mode: int) -> None:
            for row_label in BEACON_ORDER:
                point = calibration_by_mode[mode][row_label]
                for col_label in BEACON_ORDER:
                    matrix_boxes[row_label][col_label].set_val(calibration_entry_text(point, col_label))

        def publish_calibration_state() -> None:
            payload = json.dumps(
                calibration_state_to_message(calibration_by_mode, args.tag_id),
                separators=(",", ":"),
            )
            client.publish(calibration_topic, payload, retain=True)

        def capture_calibration(label: str) -> None:
            latest_sample = latest_sample_box["sample"]
            point = capture_point(latest_sample, label)
            if point is None:
                return
            calibration_by_mode[point.mode][label] = point
            refresh_matrix_inputs(point.mode)
            publish_calibration_state()
            fig.canvas.draw_idle()

        def apply_manual_calibration(_event=None) -> None:
            active_labels = active_labels_for_mode(current_mode)
            updated_rows = []

            for row_label in active_labels:
                raw_values = {
                    col_label: matrix_boxes[row_label][col_label].text.strip()
                    for col_label in active_labels
                }
                if all(not raw_value for raw_value in raw_values.values()):
                    continue

                if any(not raw_value for raw_value in raw_values.values()):
                    print("Skipping manual row {}: fill every active cell or leave the row blank.".format(row_label))
                    continue

                try:
                    row_values = {
                        col_label: int(raw_value)
                        for col_label, raw_value in raw_values.items()
                    }
                except ValueError:
                    print("Skipping manual row {}: all active cells must be integers.".format(row_label))
                    continue

                calibration_by_mode[current_mode][row_label] = build_manual_point(
                    label=row_label,
                    mode=current_mode,
                    row_values=row_values,
                )
                updated_rows.append(row_label)

            if updated_rows:
                refresh_matrix_inputs(current_mode)
                publish_calibration_state()
                fig.canvas.draw_idle()
                print("Updated manual calibration rows for mode {}: {}".format(current_mode, ", ".join(updated_rows)))

        capture_buttons["B1"].on_clicked(lambda _event: capture_calibration("B1"))
        capture_buttons["B2"].on_clicked(lambda _event: capture_calibration("B2"))
        capture_buttons["B3"].on_clicked(lambda _event: capture_calibration("B3"))
        apply_manual_button.on_clicked(apply_manual_calibration)

        current_mode = 2
        update_layout(
            current_mode,
            line_artists,
            beacon_scatter,
            beacon_labels,
            mode_text,
            capture_buttons,
            matrix_boxes,
            matrix_row_labels,
            matrix_col_labels,
            ax,
        )
        refresh_matrix_inputs(current_mode)

        def update(_frame: int):
            nonlocal current_mode

            calibration_changed = consume_incoming_messages(
                sample_queue=sample_queue,
                calibration_queue=calibration_queue,
                history=history,
                latest_sample_box=latest_sample_box,
                calibration_by_mode=calibration_by_mode,
                filter_states_by_mode=filter_states_by_mode,
                csv_file=csv_file,
            )

            if not history:
                if calibration_changed:
                    refresh_matrix_inputs(current_mode)
                return current_scatter, trail_scatter, info_text

            latest = history[-1]
            if latest.mode != current_mode:
                current_mode = latest.mode
                update_layout(
                    current_mode,
                    line_artists,
                    beacon_scatter,
                    beacon_labels,
                    mode_text,
                    capture_buttons,
                    matrix_boxes,
                    matrix_row_labels,
                    matrix_col_labels,
                    ax,
                )
                refresh_matrix_inputs(current_mode)
            elif calibration_changed:
                refresh_matrix_inputs(current_mode)

            current_calibration = calibration_by_mode[current_mode]
            positions = [estimate_position(sample, current_calibration) for sample in history if sample.mode == current_mode]
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
            if latest_position[2].startswith("CAL"):
                current_scatter.set_color(["#2a9d8f"])
            else:
                current_scatter.set_color(["#457b9d"])

            active_rows = calibration_rows_summary(current_calibration, current_mode)
            info_text.set_text(
                "tag mode = {:>8}\n"
                "estimate = {:>8}\n"
                "rssi B1  = {:>5}\n"
                "rssi B2  = {:>5}\n"
                "rssi B3  = {:>5}\n"
                "cal rows = {:>8}\n"
                "filter   = med{} gate{} x{}".format(
                    "TRIANGLE" if current_mode == 3 else "LINE",
                    latest_position[2],
                    latest_sample.rssi1,
                    latest_sample.rssi2,
                    "--" if latest_sample.rssi3 is None else latest_sample.rssi3,
                    active_rows,
                    5,
                    7,
                    3,
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
