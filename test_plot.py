from __future__ import annotations

import argparse
import sys

from serial import Serial
from serial.tools import list_ports


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
        "for example: python test_plot.py /dev/ttyACM0"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal micro:bit USB serial test reader.")
    parser.add_argument("port_arg", nargs="?", help="Optional serial port, e.g. /dev/ttyACM0")
    parser.add_argument("--port", help="Serial port for the micro:bit")
    parser.add_argument("--baud", type=int, default=9600, help="Serial baud rate")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    port = select_port(args.port or args.port_arg)
    serial_conn = Serial(port, args.baud, timeout=1)
    print(f"Reading from {port} at {args.baud} baud. Press Ctrl+C to stop.")

    try:
        while True:
            raw = serial_conn.readline()
            if not raw:
                continue
            print(raw.decode("utf-8", errors="replace").rstrip())
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        serial_conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
