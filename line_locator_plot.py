from __future__ import annotations

import argparse
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
from serial import Serial
from serial.tools import list_ports


RSSI_MIN = -95
RSSI_MAX = -40
MIN_CALIBRATION_SPAN = 6


@dataclass
class TagSample:
    timestamp: float
    rssi1: int
    rssi2: int

    @property
    def delta(self) -> int:
        return self.rssi2 - self.rssi1


@dataclass
class CalibrationPoint:
    label: str
    target_percent: float
    rssi1: int
    rssi2: int

    @property
    def delta(self) -> int:
        return self.rssi2 - self.rssi1


def clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def rssi_to_score(rssi: int) -> int:
    return int(clamp(rssi - RSSI_MIN + 1, 1, RSSI_MAX - RSSI_MIN + 1))


def estimate_raw_position(sample: TagSample) -> float:
    score1 = rssi_to_score(sample.rssi1)
    score2 = rssi_to_score(sample.rssi2)
    return float(round(score2 * 100 / (score1 + score2), 1))


def interpolate_percent(delta: int, point1: CalibrationPoint, point2: CalibrationPoint) -> float | None:
    span = point2.delta - point1.delta
    if abs(span) < MIN_CALIBRATION_SPAN:
        return None

    percent = point1.target_percent + (delta - point1.delta) * (
        point2.target_percent - point1.target_percent
    ) / span
    return round(clamp(percent, 0, 100), 1)


def has_usable_ab(calibration: dict[str, CalibrationPoint | None]) -> bool:
    point_a = calibration["A"]
    point_b = calibration["B"]
    return (
        point_a is not None
        and point_b is not None
        and abs(point_b.delta - point_a.delta) >= MIN_CALIBRATION_SPAN
    )


def has_usable_c(calibration: dict[str, CalibrationPoint | None]) -> bool:
    point_a = calibration["A"]
    point_b = calibration["B"]
    point_c = calibration["C"]
    if point_a is None or point_b is None or point_c is None:
        return False

    lower = min(point_a.delta, point_b.delta)
    upper = max(point_a.delta, point_b.delta)
    return lower < point_c.delta < upper


def estimate_position(
    sample: TagSample,
    calibration: dict[str, CalibrationPoint | None],
) -> tuple[float, str]:
    point_a = calibration["A"]
    point_b = calibration["B"]
    point_c = calibration["C"]

    if has_usable_ab(calibration):
        if has_usable_c(calibration) and point_a is not None and point_b is not None and point_c is not None:
            if (sample.delta - point_c.delta) * (point_a.delta - point_c.delta) >= 0:
                position = interpolate_percent(sample.delta, point_a, point_c)
            else:
                position = interpolate_percent(sample.delta, point_c, point_b)
            if position is not None:
                return position, "CAL-ABC"

        if point_a is not None and point_b is not None:
            position = interpolate_percent(sample.delta, point_a, point_b)
            if position is not None:
                return position, "CAL-AB"

    return estimate_raw_position(sample), "RAW"


def parse_sample(line: str) -> TagSample | None:
    parts = line.replace("\x00", "").strip().split("|")
    if not parts or parts[0] != "T":
        return None

    try:
        if len(parts) == 3:
            return TagSample(
                timestamp=time.time(),
                rssi1=int(parts[1]),
                rssi2=int(parts[2]),
            )
        if len(parts) == 4:
            return TagSample(
                timestamp=time.time(),
                rssi1=int(parts[2]),
                rssi2=int(parts[3]),
            )
        if len(parts) >= 7:
            return TagSample(
                timestamp=time.time(),
                rssi1=int(parts[2]),
                rssi2=int(parts[3]),
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
        description="Live line visualization for 2-beacon micro:bit RSSI tracking."
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


def point_summary(point: CalibrationPoint | None) -> str:
    if point is None:
        return "--"
    return f"{point.delta}"


def mode_color(mode: str) -> str:
    if mode == "CAL-ABC":
        return "#2a9d8f"
    if mode == "CAL-AB":
        return "#e9c46a"
    return "#457b9d"


def main() -> int:
    args = build_parser().parse_args()
    port = select_port(args.port or args.port_arg)
    history: deque[TagSample] = deque(maxlen=args.history)
    sample_queue: SimpleQueue[TagSample] = SimpleQueue()
    latest_sample_box: dict[str, TagSample | None] = {"sample": None}
    calibration: dict[str, CalibrationPoint | None] = {"A": None, "B": None, "C": None}

    csv_file = None
    if args.save:
        csv_file = args.save.open("a", encoding="utf-8")
        if args.save.stat().st_size == 0:
            csv_file.write("timestamp,rssi1,rssi2,delta\n")
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

    fig, ax = plt.subplots(figsize=(10, 3.8))
    fig.subplots_adjust(bottom=0.28)
    fig.canvas.manager.set_window_title("micro:bit line locator")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.45, 0.45)
    ax.set_yticks([])
    ax.set_xticks([0, 0.5, 1.0])
    ax.set_xticklabels(["Beacon 1", "Midpoint", "Beacon 2"])
    ax.set_title("Relative tag position from raw dual-beacon RSSI")
    ax.hlines(0, 0, 1, color="#264653", linewidth=3, zorder=1)
    ax.scatter([0, 1], [0, 0], s=300, marker="s", c=["#1d3557", "#1d3557"], zorder=3)
    ax.text(0, 0.14, "B1", ha="center", va="bottom", fontsize=11, weight="bold")
    ax.text(1, 0.14, "B2", ha="center", va="bottom", fontsize=11, weight="bold")

    trail_scatter = ax.scatter([], [], s=[], c=[], alpha=0.35, zorder=2)
    current_scatter = ax.scatter([0.5], [0], s=[260], c=["#adb5bd"], edgecolors="black", zorder=4)
    info_text = ax.text(
        0.02,
        0.94,
        "Waiting for tag data...",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=10,
        family="monospace",
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": "#adb5bd"},
    )

    button_a = Button(fig.add_axes([0.14, 0.07, 0.16, 0.09]), "Cal A")
    button_b = Button(fig.add_axes([0.34, 0.07, 0.16, 0.09]), "Cal B")
    button_c = Button(fig.add_axes([0.54, 0.07, 0.16, 0.09]), "Cal C")

    def capture_calibration(label: str) -> None:
        sample = latest_sample_box["sample"]
        if sample is None:
            return

        target_percent = 0.0
        if label == "B":
            target_percent = 100.0
        elif label == "C":
            target_percent = 50.0

        calibration[label] = CalibrationPoint(
            label=label,
            target_percent=target_percent,
            rssi1=sample.rssi1,
            rssi2=sample.rssi2,
        )
        fig.canvas.draw_idle()

    button_a.on_clicked(lambda _event: capture_calibration("A"))
    button_b.on_clicked(lambda _event: capture_calibration("B"))
    button_c.on_clicked(lambda _event: capture_calibration("C"))

    def update(_frame: int):
        while True:
            try:
                sample = sample_queue.get_nowait()
            except Empty:
                break

            history.append(sample)
            latest_sample_box["sample"] = sample
            if csv_file is not None:
                csv_file.write(
                    f"{sample.timestamp},{sample.rssi1},{sample.rssi2},{sample.delta}\n"
                )
                csv_file.flush()

        if not history:
            return current_scatter, trail_scatter, info_text

        positions = [estimate_position(sample, calibration) for sample in history]
        latest = history[-1]
        latest_position, latest_mode = positions[-1]

        if len(history) > 1:
            trail_offsets = [[position[0] / 100.0, 0] for position in positions[:-1]]
            trail_sizes = [60 + index * 6 for index, _ in enumerate(positions[:-1])]
            trail_colors = [mode_color(position[1]) for position in positions[:-1]]
            trail_scatter.set_offsets(trail_offsets)
            trail_scatter.set_sizes(trail_sizes)
            trail_scatter.set_color(trail_colors)
        else:
            trail_scatter.set_offsets([[-1, 0]])
            trail_scatter.set_sizes([0])
            trail_scatter.set_color(["#ffffff"])

        current_scatter.set_offsets([[latest_position / 100.0, 0]])
        current_scatter.set_sizes([280])
        current_scatter.set_color([mode_color(latest_mode)])

        info_text.set_text(
            "mode     = {:>7}\n"
            "position = {:>5.1f}%\n"
            "rssi1    = {:>5}\n"
            "rssi2    = {:>5}\n"
            "delta    = {:>5}\n"
            "cal A/B/C= {:>3} {:>3} {:>3}".format(
                latest_mode,
                latest_position,
                latest.rssi1,
                latest.rssi2,
                latest.delta,
                point_summary(calibration["A"]),
                point_summary(calibration["B"]),
                point_summary(calibration["C"]),
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
