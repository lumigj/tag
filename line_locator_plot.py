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
from serial import Serial
from serial.tools import list_ports


@dataclass
class TagSample:
    timestamp: float
    position_percent: float
    rssi1: int | None
    rssi2: int | None
    score1: int | None
    score2: int | None
    confidence: int | None

    @property
    def x(self) -> float:
        return self.position_percent / 100.0


def parse_sample(line: str) -> TagSample | None:
    parts = line.replace("\x00", "").strip().split("|")
    if not parts or parts[0] != "T":
        return None

    try:
        if len(parts) == 4:
            return TagSample(
                timestamp=time.time(),
                position_percent=float(parts[1]),
                rssi1=int(parts[2]),
                rssi2=int(parts[3]),
                score1=None,
                score2=None,
                confidence=None,
            )
        if len(parts) != 7:
            return None
        return TagSample(
            timestamp=time.time(),
            position_percent=float(parts[1]),
            rssi1=int(parts[2]),
            rssi2=int(parts[3]),
            score1=int(parts[4]),
            score2=int(parts[5]),
            confidence=int(parts[6]),
        )
    except ValueError:
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


def confidence_color(confidence: int) -> str:
    if confidence is None:
        return "#457b9d"
    if confidence >= 65:
        return "#2a9d8f"
    if confidence >= 40:
        return "#e9c46a"
    return "#e76f51"


def display_value(value: int | None) -> str:
    if value is None:
        return "--"
    return str(value)


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


def main() -> int:
    args = build_parser().parse_args()
    port = select_port(args.port or args.port_arg)
    history: deque[TagSample] = deque(maxlen=args.history)
    sample_queue: SimpleQueue[TagSample] = SimpleQueue()

    csv_file = None
    if args.save:
        csv_file = args.save.open("a", encoding="utf-8")
        if args.save.stat().st_size == 0:
            csv_file.write("timestamp,position_percent,rssi1,rssi2,score1,score2,confidence\n")
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

    fig, ax = plt.subplots(figsize=(10, 3))
    fig.canvas.manager.set_window_title("micro:bit line locator")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.45, 0.45)
    ax.set_yticks([])
    ax.set_xticks([0, 0.5, 1.0])
    ax.set_xticklabels(["Beacon 1", "Midpoint", "Beacon 2"])
    ax.set_title("Relative tag position from dual-beacon RSSI")
    ax.hlines(0, 0, 1, color="#264653", linewidth=3, zorder=1)
    ax.scatter([0, 1], [0, 0], s=300, marker="s", c=["#1d3557", "#1d3557"], zorder=3)
    ax.text(0, 0.14, "B1", ha="center", va="bottom", fontsize=11, weight="bold")
    ax.text(1, 0.14, "B2", ha="center", va="bottom", fontsize=11, weight="bold")

    trail_scatter = ax.scatter([], [], s=[], c=[], alpha=0.35, zorder=2)
    current_scatter = ax.scatter([0.5], [0], s=[260], c=["#adb5bd"], edgecolors="black", zorder=4)
    info_text = ax.text(
        0.02,
        0.92,
        "Waiting for tag data...",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=10,
        family="monospace",
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": "#adb5bd"},
    )

    def update(_frame: int):
        while True:
            try:
                sample = sample_queue.get_nowait()
            except Empty:
                break

            history.append(sample)
            if csv_file is not None:
                csv_file.write(
                    f"{sample.timestamp},{sample.position_percent},{sample.rssi1},{sample.rssi2},"
                    f"{sample.score1},{sample.score2},{sample.confidence}\n"
                )
                csv_file.flush()

        if not history:
            return current_scatter, trail_scatter, info_text

        latest = history[-1]
        if len(history) > 1:
            trail_offsets = [[sample.x, 0] for sample in list(history)[:-1]]
            trail_sizes = [60 + index * 6 for index, _ in enumerate(list(history)[:-1])]
            trail_colors = [confidence_color(sample.confidence) for sample in list(history)[:-1]]
            trail_scatter.set_offsets(trail_offsets)
            trail_scatter.set_sizes(trail_sizes)
            trail_scatter.set_color(trail_colors)
        else:
            trail_scatter.set_offsets([[-1, 0]])
            trail_scatter.set_sizes([0])
            trail_scatter.set_color(["#ffffff"])

        current_scatter.set_offsets([[latest.x, 0]])
        current_scatter.set_sizes([280])
        current_scatter.set_color([confidence_color(latest.confidence)])

        info_text.set_text(
            "position = {:>5.1f}%\n"
            "rssi1    = {:>5}\n"
            "rssi2    = {:>5}\n"
            "score1   = {:>5}\n"
            "score2   = {:>5}\n"
            "conf     = {:>5}".format(
                latest.position_percent,
                display_value(latest.rssi1),
                display_value(latest.rssi2),
                display_value(latest.score1),
                display_value(latest.score2),
                "--" if latest.confidence is None else str(latest.confidence) + "%",
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
