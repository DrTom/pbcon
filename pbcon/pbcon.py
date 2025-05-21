#!/usr/bin/env python

from __future__ import annotations

import argparse
import asyncio
import datetime
import humanize
import io
import logging
import logging.handlers
import os
import urwid

from pathlib import Path
from typing import Optional, List
from pybricksdev.ble import find_device
from pybricksdev.ble.pybricks import HubCapabilityFlag
from bleak.backends.device import BLEDevice
from pybricksdev.cli import _get_script_path
from pybricksdev.compile import compile_multi_file
from pybricksdev.connections import ConnectionState
from pybricksdev.connections.pybricks import PybricksHubBLE
import time


class ConnectionStates:
    DISCONNECTED = "DISCONNECTED"
    SEARCHING = "SEARCHING"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTING = "DISCONNECTING"


hub: Hub = None

service_uuid: str = None
ble_device: BLEDevice = None


logger: logging.Logger = None

compile_update_queue: asyncio.Queue = asyncio.Queue()
compile_timestamp: int = 0

upload_queue: asyncio.Queue = asyncio.Queue()
upload_timestamp: int = 0
upload_state_queue: asyncio.Queue = asyncio.Queue()

mpy: bytes = None
opts: argparse.Namespace = None
ui: UI = None


def init_app_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        fmt='%(asctime)s.%(msecs)03d %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    os.makedirs(opts.log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/pbcon.log", maxBytes=1024**5, backupCount=5)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


class Hub(PybricksHubBLE):

    def __init__hub_logger(self):
        self.print_output = False
        self._enable_line_handler = True
        self.hub_stdout_logger = logging.getLogger("hub_stdout")
        self.hub_stdout_logger.setLevel(logging.DEBUG)
        self.hub_stdout_logger_formatter = logging.Formatter(
            fmt='%(asctime)s %(message)s', datefmt='W%W-%w-%a_%H:%M:%S')
        file_handler = logging.handlers.RotatingFileHandler(
            "logs/hub.log", maxBytes=1024**2, backupCount=5)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(self.hub_stdout_logger_formatter)
        self.hub_stdout_logger.addHandler(file_handler)

        async def log_dispatch_loop():
            while True:
                log = await self._stdout_line_queue.get()
                self.hub_stdout_logger.info(log)
        asyncio.get_event_loop().create_task(log_dispatch_loop())

    def __init__(self):
        super().__init__()
        self.hub_log_receivers: List[asyncio.Queue] = []
        self.__init__hub_logger()

    async def log_dispatch_loop(self):
        while True:
            log = await self._stdout_line_queue.get()
            logger.info(f"HUB-LOG: {log}")
            for receiver in self.hub_log_receivers:
                receiver.put_nowait(log)

    async def upload(self, program: bytes = None) -> None:
        """
        This is mosly identical to the original download_user_program method from PybricksHub

        It does not use the tqdm progress bar queue to report the progress of the ui.
        """

        from pybricksdev.ble.pybricks import PYBRICKS_COMMAND_EVENT_UUID
        import struct
        from pybricksdev.ble.pybricks import Command
        from pybricksdev.tools import chunk

        program = program or mpy

        try:
            logger.info(f"Uploading")
            timestamp = time.time()

            upload_state_queue.put_nowait(["start", None])

            # the hub tells us the max size of program that is allowed, so we can fail early
            if len(program) > self._max_user_program_size:
                raise ValueError(
                    f"program is too big ({len(program)} bytes). Hub has limit of {self._max_user_program_size} bytes."
                )

            # clear user program meta so hub doesn't try to run invalid program

            await self.client.write_gatt_char(
                PYBRICKS_COMMAND_EVENT_UUID,
                struct.pack("<BI", Command.WRITE_USER_PROGRAM_META, 0),
                response=True,
            )

            # payload is max size minus header size
            payload_size = self._max_write_size - 5

            upload_state_queue.put_nowait(["progress", 0])

            for i, c in enumerate(chunk(program, payload_size)):
                await self.client.write_gatt_char(
                    PYBRICKS_COMMAND_EVENT_UUID,
                    struct.pack(
                        f"<BI{len(c)}s",
                        Command.COMMAND_WRITE_USER_RAM,
                        i * payload_size,
                        c,
                    ),
                    response=True,
                )

                upload_state_queue.put_nowait(
                    ["progress", (i * payload_size + len(c)) / len(program)])

            # set the metadata to notify that writing was successful
            await self.client.write_gatt_char(
                PYBRICKS_COMMAND_EVENT_UUID,
                struct.pack("<BI", Command.WRITE_USER_PROGRAM_META,
                            len(program)),
                response=True,
            )

            upload_state_queue.put_nowait(["success", None])

            global upload_timestamp
            upload_timestamp = timestamp
            logger.info(f"Uploaded")

        except Exception as e:
            logger.error(f"Upload Error: {e}")
            upload_state_queue.put_nowait(["error", e])

    async def stop(self):
        await self.stop_user_program()

    async def run(self):
        logger.info("Running")
        try:
            if upload_timestamp <= compile_timestamp:
                await self.upload(mpy)
            await self.stop()
            await self.start_user_program()
            await self._wait_for_user_program_stop()
        except Exception as e:
            logger.error(e)
        finally:
            logger.info("Stopped")


class UI:
    PALETTE = [
        ("body", "black", "light gray", "standout"),
        ("bright", "dark gray", "light gray", ("bold", "standout")),
        ("button", "yellow", "black"),
        ("buttonf", "white", "black", "bold"),
        ("repl-edit", "black", "light gray"),
        ("repl-edit-f", "white", "light gray"),
        ("header", "white", "dark blue", "bold"),
        ("important", "dark blue", "light gray", ("standout", "underline")),
        ("reverse", "light gray", "black"),
        ("success", "light gray", "dark green", "standout"),
        ("warn", "light gray", "dark red", ("bold", "standout")),
    ]

    class UiHub:

        queue: asyncio.Queue = asyncio.Queue()

        log_listbox: urwid.ListBox = urwid.ListBox(urwid.SimpleListWalker([]))

        hub: Hub = None

        class ReplEditor:

            def __init__(self, hub) -> None:
                self.hub = hub

                self.history = ["# history start",]

                self.history_index = 1

                self.repl_editor: urwid.Edit = urwid.Edit(
                    multiline=True, wrap='clip')

                def recall_prev(button):
                    if self.history_index > 0:
                        self.history_index = self.history_index - 1
                    self.repl_editor.set_edit_text(
                        self.history[self.history_index])

                self.button_prev:  urwid.Button = urwid.Button(
                    "Recall Prev", on_press=recall_prev)

                def recall_next(button):
                    if self.history_index < len(self.history) - 1:
                        self.history_index = self.history_index + 1
                    self.repl_editor.set_edit_text(
                        self.history[self.history_index])

                self.button_next:  urwid.Button = urwid.Button(
                    "Recall Next", on_press=recall_next)

                def clear(button):
                    self.repl_editor.set_edit_text("")
                    self.history_index = 0

                self.button_clear: urwid.Button = urwid.Button(
                    "Clear", on_press=clear)

                def py_format(button):
                    import autopep8
                    try:
                        formatted = autopep8.fix_code(
                            self.repl_editor.get_edit_text())
                        self.repl_editor.set_edit_text(formatted)
                    except Exception as e:
                        logger.error(f"Error: {e}")

                self.button_py_format: urwid.Button = urwid.Button(
                    "PyFormat", on_press=py_format)

                def send_data(button):
                    data = self.repl_editor.get_edit_text()
                    self.history.append(data)
                    self.history_index = len(self.history) - 1
                    logger.debug("SEND: " + data)
                    asyncio.get_event_loop().create_task(
                        self.hub.write_line(data))

                self.button_send: urwid.Button = urwid.Button(
                    "Send", on_press=send_data)

                self.repl_editor_frame: urwid.Frame = urwid.Frame(
                    urwid.AttrMap(
                        urwid.Filler(
                            self.repl_editor, valign='top'),
                        'repl-edit', 'repl-edit-f'),
                    footer=urwid.AttrMap(urwid.Padding(
                        urwid.GridFlow([urwid.AttrMap(btn, "button", "buttonf")
                                        for btn in [
                                            self.button_prev,
                                            self.button_clear,
                                            self.button_next,
                                            self.button_py_format,
                                            self.button_send]],
                                       cell_width=15, h_sep=10, v_sep=1, align='center'),
                        left=1, right=1), 'header'))

    def __init_hub_log(self):

        UI.UiHub.hub = self.hub

        queue_handler = logging.handlers.QueueHandler(UI.UiHub.queue)
        queue_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            fmt='%(asctime)s %(message)s',
            datefmt='%H:%M:%S')
        self.hub.hub_stdout_logger.addHandler(queue_handler)

        async def hub_log_loop(self):
            listbox = UI.UiHub.log_listbox
            while True:
                log_record = await UI.UiHub.queue.get()
                listbox.body.append(
                    urwid.Text(formatter.format(log_record)))
                if len(listbox.body) > 100:
                    listbox.body.pop(0)
                if listbox.get_focus()[1] == len(listbox.body) - 2:
                    listbox.set_focus(
                        len(listbox.body) - 1)
                self.urwid_loop.draw_screen()

        asyncio.get_event_loop().create_task(hub_log_loop(self))

    def __init_connection_manager(self):

        connection_state_queue: asyncio.Queue = asyncio.Queue()
        connection_state_queue.put_nowait(ConnectionStates.DISCONNECTED)

        async def connection_loop(self):
            sevvice_uuid = f"c5f5{opts.id:04x}-8280-46da-89f4-6d8051e4aeef"
            while True:
                try:
                    logger.info(f"Searching for {opts.hub_name}")
                    connection_state_queue.put_nowait(
                        ConnectionStates.SEARCHING)
                    devive_or_address = await find_device(name=opts.hub_name,
                                                          service=sevvice_uuid,
                                                          timeout=opts.timeout)
                    logger.info(f"Connecting to {devive_or_address}")
                    connection_state_queue.put_nowait(
                        ConnectionStates.CONNECTING)
                    await hub.connect(devive_or_address)
                    logger.info(f"Connected to {devive_or_address}")
                    connection_state_queue.put_nowait(
                        ConnectionStates.CONNECTED)
                    while hub.connection_state_observable.value == ConnectionState.CONNECTED:
                        await asyncio.sleep(0.1)
                    logger.warning("Lost connection, reconnecting")
                    connection_state_queue.put_nowait(
                        ConnectionStates.DISCONNECTED)
                except Exception as e:
                    logger.error(f"Error: {e}")
                    raise e

        async def update_connection_state_loop(self):
            while True:
                connection_state = await connection_state_queue.get()

                # self.main_frame.header = urwid.AttrMap(
                #    urwid.Text(f"pbcon - Connection: {connection_state}"),
                #    'success' if connection_state == ConnectionStates.CONNECTED else 'warn')

                self.connection_state.original_widget.set_text(
                    f"pbcon - Connection: {connection_state}")
                self.connection_state.set_attr_map(
                    {None: 'success' if connection_state == ConnectionStates.CONNECTED else 'warn'})

                self.urwid_loop.draw_screen()

        # self.asyncio_event_loop.create_task(connection_loop(self))
        self.asyncio_event_loop.create_task(update_connection_state_loop(self))

    def __init__(self, hub: Hub):
        self.hub = hub

        repl_editor = UI.UiHub.ReplEditor(self.hub)

        self.palette = UI.PALETTE

        def show_hub_window():
            self.main_frame.body = urwid.Columns(
                [UI.UiHub.log_listbox,
                 repl_editor.repl_editor_frame])
            self.urwid_loop.draw_screen()

        def handle_keys(key):
            if key in ('q', 'Q'):
                raise urwid.ExitMainLoop()
            elif key in ('h', 'H'):
                self.main_frame.body = self.help
                self.urwid_loop.draw_screen()
            elif key in ('l', 'L'):
                show_hub_window()
            elif key in ('r', 'R'):
                self.asyncio_event_loop.create_task(hub.run())
                show_hub_window()
            elif key in ('s', 'S'):
                self.asyncio_event_loop.create_task(hub.stop())
            elif key in ('u', 'U'):
                upload_queue.put_nowait(time.time())
            else:
                logger.debug(f"Key: {key}")
                return key

        self.asyncio_event_loop = asyncio.get_event_loop()
        evl = urwid.AsyncioEventLoop(loop=self.asyncio_event_loop)

        self.help = urwid.ListBox(urwid.SimpleListWalker([
            urwid.Text("pbcon - Pybricks Controller"),
            urwid.Text(""),
            urwid.Text("h: show this help"),
            urwid.Text(""),
            urwid.Text("q: quit"),
            urwid.Text(""),
            urwid.Text("l: show the hub log"),
            urwid.Text(""),
            urwid.Text(
                "r: run the program (upload it first if upload is outdated)"),
            urwid.Text(""),
            urwid.Text("s: stop the running program"),
            urwid.Text(""),
            urwid.Text("u: upload program"),
        ]))

        self.connection_state = urwid.AttrMap(urwid.Text(""), 'warn')

        self.upload_state = urwid.AttrMap(urwid.Text("",), 'warn')

        self.main_frame = urwid.Frame(
            header=urwid.Columns([self.connection_state, self.upload_state]),
            body=self.help)

        self.__init_hub_log()

        self.__init_connection_manager()

        self.urwid_loop = urwid.MainLoop(
            widget=self.main_frame,
            palette=self.palette,
            event_loop=evl,
            unhandled_input=handle_keys)

        # self.urwid_loop.screen.set_terminal_properties(colors=256)

    async def update_compile_loop(self):

        async def update_timestamp(timestamp, utext):
            while timestamp == compile_timestamp:
                human_delta = humanize.naturaldelta(
                    datetime.datetime.now() - datetime.datetime.fromtimestamp(timestamp))
                human_size = humanize.naturalsize(len(mpy)) if mpy else "0"
                utext.set_text(
                    f"{opts.file.name} {human_size}, {human_delta} ago")
                self.urwid_loop.draw_screen()
                await asyncio.sleep(1)

        while True:
            update = await compile_update_queue.get()
            logger.info(f"Compile update: {update}")
            timestamp = update['last_compiled']
            module_list = []
            if update['modules_missing']:
                for m in update['modules_missing']:
                    module_list.append(urwid.AttrMap(urwid.Text(m), 'warn'))
            main_text = urwid.Text("")
            self.main_frame.footer = urwid.AttrMap(urwid.Columns(
                [main_text, urwid.Columns(module_list)]), 'success' if not update['modules_missing'] else 'warn')
            self.urwid_loop.draw_screen()
            self.asyncio_event_loop.create_task(
                update_timestamp(timestamp, main_text))
            await asyncio.sleep(0.1)

    async def update_upload_progress_loop(self):

        last_upload_id = None

        def set_attr(attribute):
            self.upload_state.set_attr_map({None: attribute})

        def set_text(str):
            self.upload_state.original_widget.set_text(str)

        async def update_timestamp_fn(my_upload_id, text_widget):
            while last_upload_id == my_upload_id:
                human_delta = humanize.naturaldelta(
                    datetime.datetime.now() - datetime.datetime.fromtimestamp(upload_timestamp))
                text_widget.set_text(f"Uploaded: {human_delta} ago")
                set_attr('success' if upload_timestamp >
                         compile_timestamp else 'warn')
                self.urwid_loop.draw_screen()
                await asyncio.sleep(1)

        while True:
            type, value = await upload_state_queue.get()
            if type == "start":
                set_attr('warn')
                last_upload_id = time.time()
                self.urwid_loop.draw_screen()
            if type == "progress":
                set_text(f"Uploading: {value * 100:.0f}%")
                set_attr('warn')
                self.urwid_loop.draw_screen()
            elif type == "success":
                self.upload_state.original_widget.set_text(
                    f"Uploaded")
                self.urwid_loop.draw_screen()
                last_upload_id = time.time()
                self.asyncio_event_loop.create_task(
                    update_timestamp_fn(last_upload_id, self.upload_state.original_widget))
            elif type == "error":
                self.upload_state.original_widget.set_text(
                    f"Upload Error: {value}")
                self.urwid_loop.draw_screen()


async def main_loop():
    while True:
        await asyncio.sleep(1)


async def compile_loop():
    global compile_timestamp
    modules_missing = []
    modules = []

    def update_modules(file_path: str):
        global compile_timestamp
        nonlocal modules_missing, modules
        previous_modules = modules
        previous_modules_missing = modules_missing
        modules_missing = []
        modules = []
        from modulefinder import ModuleFinder
        import time
        search_path = [os.path.dirname(file_path)]
        mf = ModuleFinder(search_path)
        try:
            mf.run_script(file_path)
        except Exception as e:
            logger.error(f"Module Error: {e}")
            modules_missing.append(file_path)
        for name, module in mf.modules.items():
            if module.__file__ is None:
                modules_missing.append(name)
            else:
                modules.append(os.path.relpath(module.__file__))
        for name in mf.any_missing():
            if name.startswith("pybricks"):
                continue
            if name in ["ujson", "umath", "uselect", "usys"]:
                continue
            modules_missing.append(name)
        if previous_modules != modules or previous_modules_missing != modules_missing:
            logger.info(f"Modules changed: {modules_missing} {modules}")
            compile_timestamp = 0

    async def compile(file_path: str):
        global mpy
        try:
            mpy = await compile_multi_file(file_path, (6, 1))
            logger.info(f"Compiled {opts.file.name}")
        except Exception as e:
            # there are some erros that are not found by the modulefinder
            # but detected only by the compiler, catch them here
            try:
                modules.remove(file_path)
            except Exception:
                pass
            modules_missing.append(file_path)
            mpy = None
            logger.error(f"Compilation Error: {e}")

    while True:
        await asyncio.sleep(1)
        update_modules(opts.file.name)
        # logger.info(f"Checking {modules}")
        try:
            last_modified = max([os.path.getmtime(p) for p in modules])
        except Exception as e:
            last_modified = os.path.getmtime(opts.file.name)
        if compile_timestamp < last_modified:
            logger.info(f"Compiling {opts.file.name}")
            compile_timestamp = int(time.time())
            await compile(opts.file.name)
            compile_update_queue.put_nowait({'last_compiled': compile_timestamp,
                                             'main': opts.file.name,
                                             'modules': modules,
                                             'modules_missing': modules_missing})


async def upload_loop():
    global upload_timestamp

    while True:
        timestamp = await upload_queue.get()
        if timestamp > upload_timestamp:
            await hub.upload()


def parse_args():

    parser = argparse.ArgumentParser(
        description="Connects to a Pybricks device, upload and runs a program.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--timeout", type=int, default=300,
                        help="Timeout in seconds for the connection.")
    parser.add_argument(
        '--log-dir', help="relative path to the log dir", type=str, default='logs')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument(
        "--id", type=int, default=1,
        help="Sub id of the Pybricks service.",)
    parser.add_argument('hub_name', help='Name of the hub')
    parser.add_argument('file', type=argparse.FileType(),
                        help='Path to the file')
    return parser.parse_args()


async def main():
    global hub, logger, opts, ui, service_uuid, ble_device
    try: 
        opts = parse_args()
        logger = init_app_logger()
        sevvice_uuid = f"c5f5{opts.id:04x}-8280-46da-89f4-6d8051e4aeef"
        ble_device = await find_device(name=opts.hub_name,
                                    service=sevvice_uuid,
                                    timeout=opts.timeout)
        hub = Hub(ble_device)
        ui = UI(hub)
        ui.asyncio_event_loop.create_task(main_loop())
        ui.asyncio_event_loop.create_task(ui.update_compile_loop())
        ui.asyncio_event_loop.create_task(ui.update_upload_progress_loop())
        ui.asyncio_event_loop.create_task(compile_loop())
        ui.asyncio_event_loop.create_task(upload_loop())
    except asyncio.TimeoutError:
        logger.error("Timeout Error: Bluetooth Device not found")


if __name__ == "__main__":
    asyncio.run(main())
