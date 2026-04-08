from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from statistics import median

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.collections import PathCollection
from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button, TextBox
from serial import Serial
from serial.tools import list_ports


RSSI_MIN = -95
RSSI_MAX = -40
MIN_CALIBRATION_SPAN = 4
FILTER_WINDOW = 5
FILTER_GATE_DB = 7
FILTER_CANDIDATE_DB = 4
FILTER_CONFIRM_COUNT = 3
FILTER_SMOOTHING = 0.3
TRIANGLE_SIDE = 0.70
TRIANGLE_BASE_Y = 0.14
TRIANGLE_HEIGHT = TRIANGLE_SIDE * math.sqrt(3) / 2
BEACON_ORDER = ("B1", "B2", "B3")
BEACON_COORDS = {
    "B1": (0.50 - TRIANGLE_SIDE / 2, TRIANGLE_BASE_Y),
    "B2": (0.50 + TRIANGLE_SIDE / 2, TRIANGLE_BASE_Y),
    "B3": (0.50, TRIANGLE_BASE_Y + TRIANGLE_HEIGHT),
}


@dataclass
class TagSample:
    timestamp: float
    mode: int
    rssi1: int
    rssi2: int
    rssi3: int | None


@dataclass
class CalibrationPoint:
    label: str
    mode: int
    rssi1: int
    rssi2: int
    rssi3: int | None


@dataclass
class BeaconFilterState:
    window: deque[int]
    stable_value: float | None
    candidate_value: float | None
    candidate_count: int


def clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def rssi_to_score(rssi: int) -> float:
    return clamp(rssi - RSSI_MIN + 1, 1, RSSI_MAX - RSSI_MIN + 1)


def new_filter_state() -> BeaconFilterState:
    return BeaconFilterState(
        window=deque(maxlen=FILTER_WINDOW),
        stable_value=None,
        candidate_value=None,
        candidate_count=0,
    )


def rssi_for_label(sample: TagSample | CalibrationPoint, label: str) -> int | None:
    if label == "B1":
        return sample.rssi1
    if label == "B2":
        return sample.rssi2
    return sample.rssi3


def active_labels_for_mode(mode: int) -> tuple[str, ...]:
    if mode == 3:
        return BEACON_ORDER
    return BEACON_ORDER[:2]


def parse_sample(line: str) -> TagSample | None:
    parts = line.replace("\x00", "").strip().split("|")
    if not parts:
        return None

    try:
        if len(parts) == 3 and parts[0] == "L":
            return TagSample(
                timestamp=time.time(),
                mode=2,
                rssi1=int(parts[1]),
                rssi2=int(parts[2]),
                rssi3=None,
            )
        if len(parts) == 3:
            if parts[0] != "T":
                return None
            return TagSample(
                timestamp=time.time(),
                mode=2,
                rssi1=int(parts[1]),
                rssi2=int(parts[2]),
                rssi3=None,
            )
        if len(parts) == 4 and parts[0] == "T" and parts[1] in {"2", "L"}:
            return TagSample(
                timestamp=time.time(),
                mode=2,
                rssi1=int(parts[2]),
                rssi2=int(parts[3]),
                rssi3=None,
            )
        if len(parts) == 4 and parts[0] == "T":
            return TagSample(
                timestamp=time.time(),
                mode=3,
                rssi1=int(parts[1]),
                rssi2=int(parts[2]),
                rssi3=int(parts[3]),
            )
        if len(parts) == 5 and parts[0] == "T" and parts[1] in {"3", "T"}:
            return TagSample(
                timestamp=time.time(),
                mode=3,
                rssi1=int(parts[2]),
                rssi2=int(parts[3]),
                rssi3=int(parts[4]),
            )
        if len(parts) >= 7 and parts[0] == "T":
            return TagSample(
                timestamp=time.time(),
                mode=2,
                rssi1=int(parts[2]),
                rssi2=int(parts[3]),
                rssi3=None,
            )
    except ValueError:
        return None

    return None


def select_port(explicit_port: str | None) -> str:
    if explicit_port:
        return explicit_port

    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        return sys.argv[1]

    candidate_ports = []
    for port in list_ports.comports():
        device = port.device or ""
        description = (port.description or "").lower()
        hwid = (port.hwid or "").lower()

        if (
            "arduino" in description
            or "usb" in description
            or "acm" in device.lower()
            or "usb" in hwid
        ):
            candidate_ports.insert(0, device)
        else:
            candidate_ports.append(device)

    for port in candidate_ports:
        try:
            test_serial = Serial(port=port, baudrate=9600, timeout=0.1)
            test_serial.close()
            return port
        except Exception:
            continue

    raise SystemExit(
        "No usable serial port found. Pass the port explicitly, "
        "for example: python line_locator_plot.py /dev/ttyACM0"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live line/triangle visualization for micro:bit RSSI tracking."
    )
    parser.add_argument("port_arg", nargs="?", help="Optional serial port, e.g. /dev/ttyACM0")
    parser.add_argument("--port", help="Serial port for the receiver micro:bit, e.g. /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=9600, help="Serial baud rate")
    parser.add_argument("--history", type=int, default=20, help="Number of past points to keep on screen")
    parser.add_argument(
        "--save",
        type=Path,
        help="Optional CSV file path to append raw samples while plotting",
    )
    return parser


def read_samples_forever(
    serial_conn: Serial,
    sample_queue: SimpleQueue[TagSample],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            raw = serial_conn.readline()
        except Exception:
            break

        if not raw:
            continue

        line = raw.decode("utf-8", errors="ignore").strip()
        sample = parse_sample(line)
        if sample is None:
            continue
        sample_queue.put(sample)


def capture_point(latest_sample: TagSample | None, label: str) -> CalibrationPoint | None:
    if latest_sample is None:
        return None
    if label == "B3" and latest_sample.mode != 3:
        return None
    return CalibrationPoint(
        label=label,
        mode=latest_sample.mode,
        rssi1=latest_sample.rssi1,
        rssi2=latest_sample.rssi2,
        rssi3=latest_sample.rssi3,
    )


def calibration_entry_text(point: CalibrationPoint | None, label: str) -> str:
    if point is None:
        return ""
    value = rssi_for_label(point, label)
    if value is None:
        return ""
    return str(value)


def build_manual_point(
    label: str,
    mode: int,
    row_values: dict[str, int],
) -> CalibrationPoint:
    return CalibrationPoint(
        label=label,
        mode=mode,
        rssi1=row_values["B1"],
        rssi2=row_values["B2"],
        rssi3=row_values["B3"] if mode == 3 else None,
    )


def calibration_rows_summary(calibration: dict[str, CalibrationPoint | None], mode: int) -> str:
    return " ".join(
        label if calibration[label] is not None else "--"
        for label in active_labels_for_mode(mode)
    )


def raw_line_position(sample: TagSample) -> tuple[float, float]:
    score1 = rssi_to_score(sample.rssi1)
    score2 = rssi_to_score(sample.rssi2)
    total = score1 + score2
    if total <= 0:
        return 0.50, 0.50
    x = BEACON_COORDS["B1"][0] + (BEACON_COORDS["B2"][0] - BEACON_COORDS["B1"][0]) * (score2 / total)
    y = BEACON_COORDS["B1"][1] + (BEACON_COORDS["B2"][1] - BEACON_COORDS["B1"][1]) * (score2 / total)
    return round(x, 4), round(y, 4)


def raw_triangle_position(sample: TagSample) -> tuple[float, float]:
    score1 = rssi_to_score(sample.rssi1)
    score2 = rssi_to_score(sample.rssi2)
    score3 = rssi_to_score(sample.rssi3 if sample.rssi3 is not None else RSSI_MIN)
    total = score1 + score2 + score3
    if total <= 0:
        return BEACON_COORDS["B1"]
    x = (
        BEACON_COORDS["B1"][0] * score1
        + BEACON_COORDS["B2"][0] * score2
        + BEACON_COORDS["B3"][0] * score3
    ) / total
    y = (
        BEACON_COORDS["B1"][1] * score1
        + BEACON_COORDS["B2"][1] * score2
        + BEACON_COORDS["B3"][1] * score3
    ) / total
    return round(x, 4), round(y, 4)


def filter_rssi(filter_state: BeaconFilterState, raw_rssi: int) -> int:
    filter_state.window.append(raw_rssi)
    median_value = float(median(filter_state.window))

    if filter_state.stable_value is None:
        filter_state.stable_value = median_value
        return int(round(filter_state.stable_value))

    if abs(median_value - filter_state.stable_value) <= FILTER_GATE_DB:
        filter_state.stable_value = (
            filter_state.stable_value * (1 - FILTER_SMOOTHING) + median_value * FILTER_SMOOTHING
        )
        filter_state.candidate_value = None
        filter_state.candidate_count = 0
        return int(round(filter_state.stable_value))

    if filter_state.candidate_value is None or abs(median_value - filter_state.candidate_value) > FILTER_CANDIDATE_DB:
        filter_state.candidate_value = median_value
        filter_state.candidate_count = 1
        return int(round(filter_state.stable_value))

    filter_state.candidate_value = (
        filter_state.candidate_value * filter_state.candidate_count + median_value
    ) / (filter_state.candidate_count + 1)
    filter_state.candidate_count += 1

    if filter_state.candidate_count >= FILTER_CONFIRM_COUNT:
        filter_state.stable_value = filter_state.candidate_value
        filter_state.candidate_value = None
        filter_state.candidate_count = 0

    return int(round(filter_state.stable_value))


def filter_sample(
    raw_sample: TagSample,
    filter_states: dict[str, BeaconFilterState],
) -> TagSample:
    filtered_rssi1 = filter_rssi(filter_states["B1"], raw_sample.rssi1)
    filtered_rssi2 = filter_rssi(filter_states["B2"], raw_sample.rssi2)
    filtered_rssi3 = None
    if raw_sample.mode == 3 and raw_sample.rssi3 is not None:
        filtered_rssi3 = filter_rssi(filter_states["B3"], raw_sample.rssi3)

    return TagSample(
        timestamp=raw_sample.timestamp,
        mode=raw_sample.mode,
        rssi1=filtered_rssi1,
        rssi2=filtered_rssi2,
        rssi3=filtered_rssi3,
    )


def calibrated_strength(sample: TagSample, calibration: dict[str, CalibrationPoint | None], label: str) -> float | None:
    point = calibration[label]
    if point is None:
        return None

    current = rssi_for_label(sample, label)
    near = rssi_for_label(point, label)
    if current is None or near is None:
        return None

    far_values = []
    for other_label in active_labels_for_mode(sample.mode):
        other_point = calibration[other_label]
        if other_label == label or other_point is None:
            continue
        other_value = rssi_for_label(other_point, label)
        if other_value is not None:
            far_values.append(other_value)

    if far_values:
        far = sum(far_values) / len(far_values)
    else:
        far = RSSI_MIN

    span = near - far
    if span < MIN_CALIBRATION_SPAN:
        return None

    return clamp((current - far) / span, 0.0, 1.0)


def calibrated_line_position(
    sample: TagSample,
    calibration: dict[str, CalibrationPoint | None],
) -> tuple[float, float] | None:
    strength1 = calibrated_strength(sample, calibration, "B1")
    strength2 = calibrated_strength(sample, calibration, "B2")
    if strength1 is None or strength2 is None or strength1 + strength2 <= 0:
        return None

    ratio = strength2 / (strength1 + strength2)
    x = BEACON_COORDS["B1"][0] + (BEACON_COORDS["B2"][0] - BEACON_COORDS["B1"][0]) * ratio
    y = BEACON_COORDS["B1"][1] + (BEACON_COORDS["B2"][1] - BEACON_COORDS["B1"][1]) * ratio
    return round(x, 4), round(y, 4)


def calibrated_triangle_position(
    sample: TagSample,
    calibration: dict[str, CalibrationPoint | None],
) -> tuple[float, float] | None:
    strength1 = calibrated_strength(sample, calibration, "B1")
    strength2 = calibrated_strength(sample, calibration, "B2")
    strength3 = calibrated_strength(sample, calibration, "B3")
    if (
        strength1 is None
        or strength2 is None
        or strength3 is None
        or strength1 + strength2 + strength3 <= 0
    ):
        return None

    total = strength1 + strength2 + strength3
    x = (
        BEACON_COORDS["B1"][0] * strength1
        + BEACON_COORDS["B2"][0] * strength2
        + BEACON_COORDS["B3"][0] * strength3
    ) / total
    y = (
        BEACON_COORDS["B1"][1] * strength1
        + BEACON_COORDS["B2"][1] * strength2
        + BEACON_COORDS["B3"][1] * strength3
    ) / total
    return round(x, 4), round(y, 4)


def estimate_position(
    sample: TagSample,
    calibration: dict[str, CalibrationPoint | None],
) -> tuple[float, float, str]:
    if sample.mode == 3:
        calibrated = calibrated_triangle_position(sample, calibration)
        if calibrated is not None:
            return calibrated[0], calibrated[1], "CAL-B123"
        raw = raw_triangle_position(sample)
        return raw[0], raw[1], "RAW-B123"

    calibrated = calibrated_line_position(sample, calibration)
    if calibrated is not None:
        return calibrated[0], calibrated[1], "CAL-B12"
    raw = raw_line_position(sample)
    return raw[0], raw[1], "RAW-B12"


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

    ordered = list(BEACON_ORDER)
    coords = BEACON_COORDS
    beacon_scatter.set_offsets([coords[label] for label in ordered])
    beacon_scatter.set_sizes([300] * len(ordered))

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


def main() -> int:
    args = build_parser().parse_args()
    port = select_port(args.port or args.port_arg)
    history: deque[TagSample] = deque(maxlen=args.history)
    sample_queue: SimpleQueue[TagSample] = SimpleQueue()
    latest_sample_box: dict[str, TagSample | None] = {"sample": None}
    calibration_by_mode: dict[int, dict[str, CalibrationPoint | None]] = {
        2: {"B1": None, "B2": None, "B3": None},
        3: {"B1": None, "B2": None, "B3": None},
    }
    filter_states_by_mode: dict[int, dict[str, BeaconFilterState]] = {
        2: {"B1": new_filter_state(), "B2": new_filter_state(), "B3": new_filter_state()},
        3: {"B1": new_filter_state(), "B2": new_filter_state(), "B3": new_filter_state()},
    }

    csv_file = None
    if args.save:
        csv_file = args.save.open("a", encoding="utf-8")
        if args.save.stat().st_size == 0:
            csv_file.write("timestamp,mode,rssi1,rssi2,rssi3\n")
            csv_file.flush()

    serial_conn = Serial(port, args.baud, timeout=1)
    print(f"Reading receiver data from {port} at {args.baud} baud.")
    stop_event = threading.Event()
    reader_thread = threading.Thread(
        target=read_samples_forever,
        args=(serial_conn, sample_queue, stop_event),
        daemon=True,
    )
    reader_thread.start()

    fig, ax = plt.subplots(figsize=(11.6, 7.8))
    fig.subplots_adjust(left=0.26, right=0.94, bottom=0.46, top=0.82)
    fig.canvas.manager.set_window_title("micro:bit locator")
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
    current_scatter = ax.scatter([0.5], [TRIANGLE_BASE_Y + TRIANGLE_HEIGHT / 3], s=[280], c=["#adb5bd"], edgecolors="black", zorder=4)
    info_text = fig.text(
        0.08,
        0.76,
        "Waiting for tag data...",
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
        label: fig.text(0.08, y + 0.0275, f"Near {label}", ha="left", va="center", fontsize=10, weight="bold")
        for label, y in matrix_row_positions.items()
    }
    capture_buttons = {
        label: Button(fig.add_axes([0.17, y, 0.12, 0.055]), f"Cap {label}")
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

    def capture_calibration(label: str) -> None:
        latest_sample = latest_sample_box["sample"]
        point = capture_point(latest_sample, label)
        if point is None:
            return
        calibration_by_mode[point.mode][label] = point
        refresh_matrix_inputs(point.mode)
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
                print(f"Skipping manual row {row_label}: fill every active cell or leave the row blank.")
                continue

            try:
                row_values = {
                    col_label: int(raw_value)
                    for col_label, raw_value in raw_values.items()
                }
            except ValueError:
                print(f"Skipping manual row {row_label}: all active cells must be integers.")
                continue

            calibration_by_mode[current_mode][row_label] = build_manual_point(
                label=row_label,
                mode=current_mode,
                row_values=row_values,
            )
            updated_rows.append(row_label)

        if updated_rows:
            refresh_matrix_inputs(current_mode)
            fig.canvas.draw_idle()
            print(f"Updated manual calibration rows for mode {current_mode}: {', '.join(updated_rows)}")

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
                    f"{raw_sample.timestamp},{raw_sample.mode},{raw_sample.rssi1},{raw_sample.rssi2},{raw_sample.rssi3}\n"
                )
                csv_file.flush()

        if not history:
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
                FILTER_WINDOW,
                FILTER_GATE_DB,
                FILTER_CONFIRM_COUNT,
            )
        )
        return current_scatter, trail_scatter, info_text

    animation = FuncAnimation(fig, update, interval=80, cache_frame_data=False)
    try:
        plt.show()
    finally:
        stop_event.set()
        animation.event_source.stop()
        serial_conn.close()
        reader_thread.join(timeout=1)
        if csv_file is not None:
            csv_file.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
