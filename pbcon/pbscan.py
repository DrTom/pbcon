#!/usr/bin/env python

import argparse
import asyncio
import bleak
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scans for Pybricks devices and prints their names and RSSI values.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "--id",
        type=int,
        default=1,
        help="Sub id of the Pybricks service to scan for.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for the scan.",
    )

    parser.add_argument(
        '--full',
        action=argparse.BooleanOptionalAction,
        help="Print full BLE data for each device.",
    )

    return parser.parse_args()


def scan(opts):

    print("Scanning for Pybricks devices... (press Ctrl+C to stop)")

    def pybricks_uuid(short: int) -> str:
        return f"c5f5{short:04x}-8280-46da-89f4-6d8051e4aeef"

    def print_device(device: BLEDevice, adv_data: AdvertisementData):
        print("\n")
        print("-" * 40)
        print("name: '{}' rssi: {}".format(device.name, adv_data.rssi))
        if opts.full:
            print("  device: {}".format(device))
            for name, value in adv_data._asdict().items():
                if not name.startswith("platform"):
                    print("  {} => {}".format(name, value))
        print("-" * 40)

    async def scan():
        devices = set()

        def callback(device: BLEDevice, adv_data: AdvertisementData):
            if device.address not in devices:
                print_device(device, adv_data)
                devices.add(device.address)

        scanner = bleak.BleakScanner(
            service_uuids=[pybricks_uuid(opts.id)],
            detection_callback=callback)
        await scanner.start()
        await asyncio.sleep(opts.timeout)
        await scanner.stop()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(scan())
    loop.close()


def main():
    opts = parse_args()
    print("Options:", opts)
    scan(opts)


if __name__ == "__main__":
    main()
