from __future__ import annotations

import argparse
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from statistics import median

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
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
BEACON_COORDS = {
    "B1": (0.50, 0.90),
    "B2": (0.10, 0.12),
    "B3": (0.90, 0.12),
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


def calibration_summary(point: CalibrationPoint | None, label: str) -> str:
    if point is None:
        return "--"
    value = rssi_for_label(point, label)
    if value is None:
        return "--"
    return str(value)


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
    for other_label, other_point in calibration.items():
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
    line_artists: list,
    beacon_scatter,
    beacon_labels: dict[str, any],
    mode_text,
    button_b3,
    ax,
) -> None:
    for line in line_artists:
        line.set_data([], [])

    ordered = ["B1", "B2", "B3"]
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
        button_b3.ax.set_facecolor("#d9f2ec")
        button_b3.label.set_color("black")
        ax.set_title("Triangle packets use B1, B2, and B3")
    else:
        line_artists[0].set_data([coords["B1"][0], coords["B2"][0]], [coords["B1"][1], coords["B2"][1]])
        beacon_scatter.set_color(["#1d3557", "#1d3557", "#cbd5e1"])
        mode_text.set_text("LINE MODE: B1-B2 ONLY")
        mode_text.set_color("#457b9d")
        button_b3.ax.set_facecolor("#e5e7eb")
        button_b3.label.set_color("#8a8f98")
        ax.set_title("Line packets use only B1 and B2; B3 is shown for reference")

    for label, text in beacon_labels.items():
        x, y = coords[label]
        text.set_position((x, y + 0.07))
        if mode == 2 and label == "B3":
            text.set_color("#8a8f98")
        else:
            text.set_color("black")
        text.set_visible(True)


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

    fig, ax = plt.subplots(figsize=(10, 5.4))
    fig.subplots_adjust(bottom=0.26)
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
    current_scatter = ax.scatter([0.5], [0.12], s=[280], c=["#adb5bd"], edgecolors="black", zorder=4)
    info_text = ax.text(
        0.02,
        0.97,
        "Waiting for tag data...",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=10,
        family="monospace",
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": "#adb5bd"},
    )
    mode_text = ax.text(
        0.98,
        0.97,
        "",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=11,
        weight="bold",
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#cbd5e1"},
    )

    button_b1 = Button(fig.add_axes([0.10, 0.08, 0.18, 0.08]), "Cal B1")
    button_b2 = Button(fig.add_axes([0.32, 0.08, 0.18, 0.08]), "Cal B2")
    button_b3 = Button(fig.add_axes([0.54, 0.08, 0.18, 0.08]), "Cal B3")

    def capture_calibration(label: str) -> None:
        latest_sample = latest_sample_box["sample"]
        point = capture_point(latest_sample, label)
        if point is None:
            return
        calibration_by_mode[point.mode][label] = point
        fig.canvas.draw_idle()

    button_b1.on_clicked(lambda _event: capture_calibration("B1"))
    button_b2.on_clicked(lambda _event: capture_calibration("B2"))
    button_b3.on_clicked(lambda _event: capture_calibration("B3"))

    current_mode = 2
    update_layout(current_mode, line_artists, beacon_scatter, beacon_labels, mode_text, button_b3, ax)

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
            update_layout(current_mode, line_artists, beacon_scatter, beacon_labels, mode_text, button_b3, ax)

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

        info_text.set_text(
            "tag mode = {:>8}\n"
            "estimate = {:>8}\n"
            "rssi B1  = {:>5}\n"
            "rssi B2  = {:>5}\n"
            "rssi B3  = {:>5}\n"
            "cal B1   = {:>5}\n"
            "cal B2   = {:>5}\n"
            "cal B3   = {:>5}\n"
            "filter   = med{} gate{} x{}".format(
                "TRIANGLE" if current_mode == 3 else "LINE",
                latest_position[2],
                latest_sample.rssi1,
                latest_sample.rssi2,
                "--" if latest_sample.rssi3 is None else latest_sample.rssi3,
                calibration_summary(current_calibration["B1"], "B1"),
                calibration_summary(current_calibration["B2"], "B2"),
                calibration_summary(current_calibration["B3"], "B3"),
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
