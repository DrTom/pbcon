#!/usr/bin/env python

from __future__ import annotations
import datetime
import json
import sys

import asyncio
import logging
import os
import pybricksdev.compile

from pathlib import Path
from typing import Optional
from pybricksdev.ble import find_device
from pybricksdev.ble.pybricks import HubCapabilityFlag
from pybricksdev.cli import _get_script_path
from pybricksdev.compile import compile_multi_file
from pybricksdev.connections import ConnectionState
from pybricksdev.connections.pybricks import PybricksHub


event_loop = asyncio.get_event_loop()
hub: Hub = None
is_running = False
logger: logging.Logger = None
mpy: bytes = None
opts = None


def init_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s - %(message)s')
    console_handler = logging.StreamHandler()
    if opts.debug:
        console_handler.setLevel(logging.DEBUG)
    else:
        console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


class Hub(PybricksHub):

    def __init__(self):
        super().__init__()
        self.print_output = False
        self._enable_line_handler = True

    async def upload(
        self,
        line_handler: bool = True,
    ) -> None:
        """
        Compiles and uploads a user program. See PybricksHub.run() for details. 
        """
        if self.connection_state_observable.value != ConnectionState.CONNECTED:
            raise RuntimeError("not connected")
        # Reset output buffer
        self.log_file = None
        self.output = []
        self._stdout_buf.clear()
        await self.download_user_program(mpy)


def parse_args():
    import argparse
    parser = argparse.ArgumentParser(
        description="Connects to a Pybricks device, upload and runs a program.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '--data-log-dir', help="relative path to log dir", type=str, default='logs')
    parser.add_argument('--data-log-prefix',
                        help="prefix for data log files", default='run-data_')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument(
        "--id", type=int, default=1,
        help="Sub id of the Pybricks service.",)
    parser.add_argument('hub_name', help='Name of the hub')
    parser.add_argument('file', type=argparse.FileType(),
                        help='Path to the file')
    parser.add_argument('--connection_timeout', type=int, default=60,
                        help='Timeout in seconds for the connection')
    return parser.parse_args()


def module_finder(file_path):
    from modulefinder import ModuleFinder
    search_path = [os.path.dirname(file_path)]
    finder = ModuleFinder(search_path)
    finder.run_script(file_path)
    return finder


async def compile(file_path):
    global mpy
    logger.info(f"Compiling {file_path}")
    for name in module_finder(file_path).any_missing():
        if name.startswith("pybricks"):
            continue
        if name in ['ujson', 'umath']:
            continue
        logger.warning(f"Missing module: {name}")
    mpy = await compile_multi_file(file_path, (6, 1))
    # pybricksdev.compile.print_mpy(mpy)
    logger.info(f"Compiled {file_path}, size: {len(mpy)} bytes")


async def upload():
    await hub.upload()


async def hub_output_logger_loop():
    current_log_file = None
    first_line = True

    def close_current_log_file():
        nonlocal current_log_file
        try:
            if current_log_file:
                current_log_file.write("]")
                current_log_file.close()
                current_log_file = None
        except Exception as e:
            logger.error(e)

    def start_new_log_file():
        nonlocal current_log_file, first_line
        close_current_log_file()
        first_line = True
        ts = datetime.datetime.now().replace(microsecond=0).isoformat()
        log_file_path = os.path.join(
            opts.data_log_dir, f"{opts.data_log_prefix}{ts}.json")
        current_log_file = open(log_file_path, "w")
        symlink_path = os.path.join(
            opts.data_log_dir, f"{opts.data_log_prefix}latest.json")
        Path(symlink_path).unlink(missing_ok=True)
        os.symlink(os.path.basename(log_file_path), symlink_path)

    while is_running:
        line = await hub.read_line()
        try:
            data = json.loads(line)
            logger.debug("DATA: " + json.dumps(data))
            if not type(data) is dict:
                logger.info("HUB OUTPUT: " + line)
            elif data["type"] == 'data-log-start':
                start_new_log_file()
            elif data["type"] == 'data-log':
                if current_log_file:
                    if first_line:
                        current_log_file.write("[")
                        first_line = False
                    else:
                        current_log_file.write('\n,')
                    current_log_file.write(json.dumps(data))
            elif data["type"] == 'data-log-end':
                close_current_log_file()
            else:
                logger.info("HUB DATA OUTPUT: " + json.dumps(data))
        except json.JSONDecodeError as ex:
            logger.info("HUB OUTPUT: " + line)
            continue

    close_current_log_file()


async def run():
    # TODO clean buffer and stuff,
    # see https://github.com/pybricks/pybricksdev/blob/11667cb05427b2638fb475c1561fdfa380f59998/pybricksdev/connections/pybricks.py#L531-L537
    # we might have to extend the hub class to do this
    # or this maybe can be used: https://github.com/pybricks/pybricksdev/blob/11667cb05427b2638fb475c1561fdfa380f59998/pybricksdev/connections/pybricks.py#L412
    logger.info("Running")
    try:
        await hub.start_user_program()
        await hub._wait_for_user_program_stop()
    except Exception as e:
        logger.error(e)
    finally:
        logger.info("Stopped")


async def connect():
    sevvice_uuid = f"c5f5{opts.id:04x}-8280-46da-89f4-6d8051e4aeef"
    hub = Hub()
    logger.info(f"Searching for {opts.hub_name}")
    devive_or_address = await find_device(name=opts.hub_name,
                                          service=sevvice_uuid,
                                          timeout=opts.connection_timeout)
    logger.info(f"Connecting to {devive_or_address}")
    await hub.connect(devive_or_address)
    logger.info(f"Connected to {devive_or_address}")
    return hub


async def disconnect():
    global hub
    await hub.disconnect()


async def mainloop():
    global is_running
    while is_running:
        cmd = input("Enter command: ")
        if cmd in ['q', 'quit', 'exit']:
            logger.info("Quitting")
            is_running = False
            break
        elif cmd in ['u', 'upload']:
            try:
                await compile(opts.file.name)
                await upload()
            except Exception as e:
                logger.error(e)
        elif cmd in ['r', 'run']:
            await run()
        else:
            logger.info("Invalid command")
        await asyncio.sleep(0.1)


async def main_run():
    global logger, hub, is_running, opts
    opts = parse_args()
    print(opts)
    logger = init_logger()
    logger.debug("Debug mode enabled")

    try:
        await compile(opts.file.name)
    except Exception as e:
        logger.error(e)

    hub = await connect()

    is_running = True

    event_loop.create_task(hub_output_logger_loop())
    event_loop.create_task(mainloop())

    while is_running:
        await asyncio.sleep(1)

    await disconnect()


def main():
    global event_loop, is_running
    try:
        event_loop.run_until_complete(main_run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        is_running = False
        event_loop.run_until_complete(disconnect())
    finally:
        event_loop.close()


if __name__ == "__main__":
    main()
