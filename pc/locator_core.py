from __future__ import annotations

import math
import sys
import time
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Any

from serial import Serial
from serial.tools import list_ports


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


def default_beacon_coords() -> dict[str, tuple[float, float]]:
    return {label: BEACON_COORDS[label] for label in BEACON_ORDER}


def layout_center(beacon_coords: dict[str, tuple[float, float]], mode: int) -> tuple[float, float]:
    labels = active_labels_for_mode(mode)
    x = sum(beacon_coords[label][0] for label in labels) / len(labels)
    y = sum(beacon_coords[label][1] for label in labels) / len(labels)
    return round(x, 4), round(y, 4)


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


def rssi_to_distance_proxy(rssi: int) -> float:
    # Stronger RSSI maps to a shorter proxy distance.
    return max(abs(rssi), 1)


TRIANGLE_SEARCH_STEPS = 48


def triangle_candidate_error(
    point_x: float,
    point_y: float,
    beacon_coords: dict[str, tuple[float, float]],
    proxy_distances: dict[str, float],
) -> tuple[float, float]:
    actual_distances = {
        label: math.hypot(point_x - beacon_coords[label][0], point_y - beacon_coords[label][1])
        for label in BEACON_ORDER
    }

    order_penalty = 0.0
    for label in BEACON_ORDER:
        for other_label in BEACON_ORDER:
            if proxy_distances[label] + 1e-9 < proxy_distances[other_label]:
                order_penalty += max(actual_distances[label] - actual_distances[other_label], 0.0) ** 2

    denominator = sum(proxy_distances[label] ** 2 for label in BEACON_ORDER)
    if denominator <= 1e-9:
        return order_penalty, 0.0

    scale = sum(
        actual_distances[label] * proxy_distances[label]
        for label in BEACON_ORDER
    ) / denominator
    ratio_error = sum(
        (actual_distances[label] - scale * proxy_distances[label]) ** 2
        for label in BEACON_ORDER
    )
    return order_penalty, ratio_error


def proportional_triangle_position(
    beacon_coords: dict[str, tuple[float, float]],
    distances: dict[str, float],
) -> tuple[float, float]:
    best_point = layout_center(beacon_coords, 3)
    best_score = triangle_candidate_error(best_point[0], best_point[1], beacon_coords, distances)

    for i in range(TRIANGLE_SEARCH_STEPS + 1):
        weight_b1 = i / TRIANGLE_SEARCH_STEPS
        for j in range(TRIANGLE_SEARCH_STEPS + 1 - i):
            weight_b2 = j / TRIANGLE_SEARCH_STEPS
            weight_b3 = 1.0 - weight_b1 - weight_b2

            point_x = (
                beacon_coords["B1"][0] * weight_b1
                + beacon_coords["B2"][0] * weight_b2
                + beacon_coords["B3"][0] * weight_b3
            )
            point_y = (
                beacon_coords["B1"][1] * weight_b1
                + beacon_coords["B2"][1] * weight_b2
                + beacon_coords["B3"][1] * weight_b3
            )

            score = triangle_candidate_error(point_x, point_y, beacon_coords, distances)
            if score < best_score:
                best_score = score
                best_point = (point_x, point_y)

    return round(best_point[0], 4), round(best_point[1], 4)


def new_filter_state() -> BeaconFilterState:
    return BeaconFilterState(
        window=deque(maxlen=FILTER_WINDOW),
        stable_value=None,
        candidate_value=None,
        candidate_count=0,
    )


def new_filter_states_by_mode() -> dict[int, dict[str, BeaconFilterState]]:
    return {
        2: {"B1": new_filter_state(), "B2": new_filter_state(), "B3": new_filter_state()},
        3: {"B1": new_filter_state(), "B2": new_filter_state(), "B3": new_filter_state()},
    }


def empty_calibration_state() -> dict[int, dict[str, CalibrationPoint | None]]:
    return {
        2: {"B1": None, "B2": None, "B3": None},
        3: {"B1": None, "B2": None, "B3": None},
    }


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
        "for example: python -m rpi.reader /dev/ttyACM0"
    )


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


def raw_line_position(
    sample: TagSample,
    beacon_coords: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, float]:
    coords = BEACON_COORDS if beacon_coords is None else beacon_coords
    distance1 = rssi_to_distance_proxy(sample.rssi1)
    distance2 = rssi_to_distance_proxy(sample.rssi2)
    total = distance1 + distance2
    if total <= 0:
        return 0.50, 0.50
    ratio_from_b1 = distance1 / total
    x = coords["B1"][0] + (coords["B2"][0] - coords["B1"][0]) * ratio_from_b1
    y = coords["B1"][1] + (coords["B2"][1] - coords["B1"][1]) * ratio_from_b1
    return round(x, 4), round(y, 4)


def raw_triangle_position(
    sample: TagSample,
    beacon_coords: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, float]:
    coords = BEACON_COORDS if beacon_coords is None else beacon_coords
    distances = {
        "B1": rssi_to_distance_proxy(sample.rssi1),
        "B2": rssi_to_distance_proxy(sample.rssi2),
        "B3": rssi_to_distance_proxy(sample.rssi3 if sample.rssi3 is not None else sample.rssi2),
    }
    return proportional_triangle_position(coords, distances)


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
        far = near - MIN_CALIBRATION_SPAN

    span = near - far
    if span < MIN_CALIBRATION_SPAN:
        return None

    return max(0.0, min((current - far) / span, 1.0))


def calibrated_line_position(
    sample: TagSample,
    calibration: dict[str, CalibrationPoint | None],
    beacon_coords: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    coords = BEACON_COORDS if beacon_coords is None else beacon_coords
    strength1 = calibrated_strength(sample, calibration, "B1")
    strength2 = calibrated_strength(sample, calibration, "B2")
    if strength1 is None or strength2 is None or strength1 + strength2 <= 0:
        return None

    ratio = strength2 / (strength1 + strength2)
    x = coords["B1"][0] + (coords["B2"][0] - coords["B1"][0]) * ratio
    y = coords["B1"][1] + (coords["B2"][1] - coords["B1"][1]) * ratio
    return round(x, 4), round(y, 4)


def calibrated_triangle_position(
    sample: TagSample,
    calibration: dict[str, CalibrationPoint | None],
    beacon_coords: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    coords = BEACON_COORDS if beacon_coords is None else beacon_coords
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
        coords["B1"][0] * strength1
        + coords["B2"][0] * strength2
        + coords["B3"][0] * strength3
    ) / total
    y = (
        coords["B1"][1] * strength1
        + coords["B2"][1] * strength2
        + coords["B3"][1] * strength3
    ) / total
    return round(x, 4), round(y, 4)


def estimate_position(
    sample: TagSample,
    calibration: dict[str, CalibrationPoint | None],
    beacon_coords: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, float, str]:
    if sample.mode == 3:
        calibrated = calibrated_triangle_position(sample, calibration, beacon_coords)
        if calibrated is not None:
            return calibrated[0], calibrated[1], "CAL-B123"
        raw = raw_triangle_position(sample, beacon_coords)
        return raw[0], raw[1], "RAW-B123"

    calibrated = calibrated_line_position(sample, calibration, beacon_coords)
    if calibrated is not None:
        return calibrated[0], calibrated[1], "CAL-B12"
    raw = raw_line_position(sample, beacon_coords)
    return raw[0], raw[1], "RAW-B12"


def sample_to_message(sample: TagSample, tag_id: str) -> dict[str, Any]:
    return {
        "type": "tag_sample",
        "version": 1,
        "tag_id": tag_id,
        "timestamp": sample.timestamp,
        "mode": sample.mode,
        "rssi1": sample.rssi1,
        "rssi2": sample.rssi2,
        "rssi3": sample.rssi3,
    }


def sample_from_message(payload: dict[str, Any]) -> TagSample | None:
    try:
        mode = int(payload["mode"])
        rssi3 = payload.get("rssi3")
        return TagSample(
            timestamp=float(payload.get("timestamp", time.time())),
            mode=mode,
            rssi1=int(payload["rssi1"]),
            rssi2=int(payload["rssi2"]),
            rssi3=None if rssi3 is None else int(rssi3),
        )
    except (KeyError, TypeError, ValueError):
        return None


def calibration_point_to_message(point: CalibrationPoint | None) -> dict[str, Any] | None:
    if point is None:
        return None
    return {
        "label": point.label,
        "mode": point.mode,
        "rssi1": point.rssi1,
        "rssi2": point.rssi2,
        "rssi3": point.rssi3,
    }


def calibration_point_from_message(data: dict[str, Any] | None) -> CalibrationPoint | None:
    if data is None:
        return None

    try:
        rssi3 = data.get("rssi3")
        return CalibrationPoint(
            label=str(data["label"]),
            mode=int(data["mode"]),
            rssi1=int(data["rssi1"]),
            rssi2=int(data["rssi2"]),
            rssi3=None if rssi3 is None else int(rssi3),
        )
    except (KeyError, TypeError, ValueError):
        return None


def calibration_state_to_message(
    calibration_by_mode: dict[int, dict[str, CalibrationPoint | None]],
    tag_id: str,
) -> dict[str, Any]:
    return {
        "type": "calibration_state",
        "version": 1,
        "tag_id": tag_id,
        "timestamp": time.time(),
        "modes": {
            str(mode): {
                label: calibration_point_to_message(point)
                for label, point in calibration_by_mode[mode].items()
            }
            for mode in (2, 3)
        },
    }


def calibration_state_from_message(
    payload: dict[str, Any],
) -> dict[int, dict[str, CalibrationPoint | None]] | None:
    modes_payload = payload.get("modes")
    if not isinstance(modes_payload, dict):
        return None

    calibration_by_mode = empty_calibration_state()
    for mode in (2, 3):
        mode_payload = modes_payload.get(str(mode))
        if not isinstance(mode_payload, dict):
            continue

        for label in BEACON_ORDER:
            point_payload = mode_payload.get(label)
            calibration_by_mode[mode][label] = calibration_point_from_message(point_payload)

    return calibration_by_mode
