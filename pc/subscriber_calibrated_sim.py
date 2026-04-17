from __future__ import annotations

import argparse
import math
import random
import sys
import time
from collections import deque
from pathlib import Path
from queue import SimpleQueue

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pc.locator_core import (
    TagSample,
    active_labels_for_mode,
    default_beacon_coords,
    filter_sample,
    layout_center,
    new_filter_states_by_mode,
)
from pc.subscriber_calibrated import (
    estimate_offset_position,
    offset_distance_proxy,
    update_layout,
)


MOCK_RSSI_OFFSET = 20.0
MOCK_DISTANCE_SCALE = 72.0
LINE_PHASE_SECONDS = 8.0
TRIANGLE_PHASE_SECONDS = 12.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run subscriber_calibrated.py with built-in mock locator samples instead of MQTT."
    )
    parser.add_argument("--history", type=int, default=20, help="Number of past points to keep on screen")
    parser.add_argument("--save", type=Path, help="Optional CSV file path to append generated raw samples")
    parser.add_argument("--headless", action="store_true", help="Print simulated estimates without opening the plot window")
    parser.add_argument("--duration", type=float, default=24.0, help="Headless runtime in seconds")
    parser.add_argument("--offset", type=float, default=20.0, help="Initial abs(rssi) offset used by the estimator UI")
    parser.add_argument("--sample-interval", type=float, default=0.12, help="Seconds between generated samples")
    parser.add_argument("--seed", type=int, default=4151, help="Random seed for deterministic mock noise")
    return parser


def lerp_point(a: tuple[float, float], b: tuple[float, float], ratio: float) -> tuple[float, float]:
    return (
        a[0] + (b[0] - a[0]) * ratio,
        a[1] + (b[1] - a[1]) * ratio,
    )


class MockLocatorStream:
    def __init__(
        self,
        beacon_coords: dict[str, tuple[float, float]],
        sample_interval: float,
        seed: int,
    ):
        self.beacon_coords = beacon_coords
        self.sample_interval = sample_interval
        self.rng = random.Random(seed)
        self.started_at = time.monotonic()
        self.next_emit_at = self.started_at
        self.last_script_name = "LINE-SWEEP"
        self.last_true_point = layout_center(beacon_coords, 2)

    def scenario_at(self, elapsed: float) -> tuple[int, tuple[float, float], str]:
        cycle_seconds = LINE_PHASE_SECONDS + TRIANGLE_PHASE_SECONDS
        cycle_elapsed = elapsed % cycle_seconds

        if cycle_elapsed < LINE_PHASE_SECONDS:
            phase = (cycle_elapsed / LINE_PHASE_SECONDS) * 2.0 * math.pi
            ratio = 0.1 + 0.8 * (0.5 + 0.5 * math.sin(phase))
            point = lerp_point(self.beacon_coords["B1"], self.beacon_coords["B2"], ratio)
            return 2, point, "LINE-SWEEP"

        triangle_elapsed = cycle_elapsed - LINE_PHASE_SECONDS
        phase = (triangle_elapsed / TRIANGLE_PHASE_SECONDS) * 2.0 * math.pi
        raw_weights = [
            0.2 + 0.8 * (0.5 + 0.5 * math.sin(phase)),
            0.2 + 0.8 * (0.5 + 0.5 * math.sin(phase + 2.1)),
            0.2 + 0.8 * (0.5 + 0.5 * math.sin(phase + 4.2)),
        ]
        total = sum(raw_weights)
        weight_b1, weight_b2, weight_b3 = [value / total for value in raw_weights]
        point = (
            self.beacon_coords["B1"][0] * weight_b1
            + self.beacon_coords["B2"][0] * weight_b2
            + self.beacon_coords["B3"][0] * weight_b3,
            self.beacon_coords["B1"][1] * weight_b1
            + self.beacon_coords["B2"][1] * weight_b2
            + self.beacon_coords["B3"][1] * weight_b3,
        )
        return 3, point, "TRIANGLE-ORBIT"

    def point_to_rssi(self, point: tuple[float, float], label: str) -> int:
        beacon_x, beacon_y = self.beacon_coords[label]
        distance = math.hypot(point[0] - beacon_x, point[1] - beacon_y)
        noise = self.rng.uniform(-1.6, 1.6)
        absolute_rssi = MOCK_RSSI_OFFSET + 2.0 + distance * MOCK_DISTANCE_SCALE + noise
        return -int(round(max(1.0, absolute_rssi)))

    def make_sample(self, emit_monotonic: float) -> TagSample:
        elapsed = emit_monotonic - self.started_at
        mode, point, script_name = self.scenario_at(elapsed)
        self.last_script_name = script_name
        self.last_true_point = (round(point[0], 4), round(point[1], 4))

        rssi1 = self.point_to_rssi(point, "B1")
        rssi2 = self.point_to_rssi(point, "B2")
        rssi3 = self.point_to_rssi(point, "B3") if mode == 3 else None

        return TagSample(
            timestamp=time.time(),
            mode=mode,
            rssi1=rssi1,
            rssi2=rssi2,
            rssi3=rssi3,
        )

    def pump(self, sample_queue: SimpleQueue[TagSample]) -> None:
        now = time.monotonic()
        while now >= self.next_emit_at:
            sample_queue.put(self.make_sample(self.next_emit_at))
            self.next_emit_at += self.sample_interval


def consume_generated_messages(
    sample_queue: SimpleQueue[TagSample],
    history: deque[TagSample],
    latest_sample_box: dict[str, TagSample | None],
    filter_states_by_mode: dict[int, dict[str, object]],
    csv_file,
) -> None:
    while True:
        try:
            raw_sample = sample_queue.get_nowait()
        except Exception:
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


def run() -> int:
    args = build_parser().parse_args()

    sample_queue: SimpleQueue[TagSample] = SimpleQueue()
    history: deque[TagSample] = deque(maxlen=args.history)
    latest_sample_box: dict[str, TagSample | None] = {"sample": None}
    filter_states_by_mode = new_filter_states_by_mode()
    beacon_coords = default_beacon_coords()
    offset_box = {"value": float(args.offset)}
    mock_stream = MockLocatorStream(
        beacon_coords=beacon_coords,
        sample_interval=args.sample_interval,
        seed=args.seed,
    )

    csv_file = None
    if args.save:
        csv_file = args.save.open("a", encoding="utf-8")
        if args.save.stat().st_size == 0:
            csv_file.write("timestamp,mode,rssi1,rssi2,rssi3\n")
            csv_file.flush()

    try:
        if args.headless:
            print(
                "Running simulated offset subscriber for {:.1f} seconds with offset={} seed={}...".format(
                    args.duration,
                    offset_box["value"],
                    args.seed,
                )
            )
            deadline = time.time() + args.duration
            last_reported = 0

            while time.time() < deadline:
                mock_stream.pump(sample_queue)
                consume_generated_messages(
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
                            "script={} mode={} true=({}, {}) estimate={} x={} y={} offset={} rssi=({}, {}, {})".format(
                                mock_stream.last_script_name,
                                latest_sample.mode,
                                mock_stream.last_true_point[0],
                                mock_stream.last_true_point[1],
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
        fig.canvas.manager.set_window_title("micro:bit locator simulator (offset raw)")
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
            "Waiting for simulated tag data...",
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

        fig.text(0.07, 0.23, "Offset Method Simulator", ha="left", va="center", fontsize=11, weight="bold")
        fig.text(
            0.07,
            0.205,
            "",
            ha="left",
            va="center",
            fontsize=9,
            color="#6b7280",
        )
        from matplotlib.widgets import Button, TextBox

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
            "This simulator reuses subscriber_calibrated.py logic but replaces MQTT with scripted samples.",
            ha="left",
            va="center",
            fontsize=9,
            color="#6b7280",
        )

        def refresh_offset_status() -> None:
            offset_status_text.set_text(
                "est offset = {}\nmock base  = {}\nproxy example:\n-21 -> {}\n-50 -> {}".format(
                    offset_box["value"],
                    MOCK_RSSI_OFFSET,
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

            mock_stream.pump(sample_queue)
            consume_generated_messages(
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
                "script   = {:>12}\n"
                "tag mode = {:>12}\n"
                "estimate = {:>12}\n"
                "true xy  = ({:>5}, {:>5})\n"
                "rssi B1  = {:>12}\n"
                "rssi B2  = {:>12}\n"
                "rssi B3  = {:>12}\n"
                "offset   = {:>12}\n"
                "layout   = {:>12}".format(
                    mock_stream.last_script_name,
                    "TRIANGLE" if current_mode == 3 else "LINE",
                    latest_position[2],
                    mock_stream.last_true_point[0],
                    mock_stream.last_true_point[1],
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


if __name__ == "__main__":
    raise SystemExit(run())
