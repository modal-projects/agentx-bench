"""Run the Modal Server mounted in this Sandbox."""

import os

import asyncio
import inspect
import runpy
import signal
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import modal
from modal._partial_function import (
    _find_callables_for_obj,
    _PartialFunctionFlags,
)

THIS = Path(__file__)
SERVE_FILE_ENV = "MODAL_SANDBOX_SERVE_FILE"
SERVER_PORT_ENV = "MODAL_SANDBOX_SERVER_PORT"
SERVE_FILE = os.environ.get(SERVE_FILE_ENV)
PORT = 8000


def main(argv):
    serve_path = parse_args(argv)
    server_port = get_server_port()

    # In case a previous Server start died before it could clean up.
    clear_port(server_port)

    server = load_server(serve_path)
    return asyncio.run(run_server(server, server_port))


def parse_args(argv):
    match len(argv):
        case 1 if SERVE_FILE:
            return Path(SERVE_FILE).expanduser().resolve()
        case 1:
            print(
                f"error: {SERVE_FILE_ENV} is not set; pass a Server file explicitly",
                file=sys.stderr,
            )
            sys.exit(2)
        case 2:
            return Path(argv[1]).expanduser().resolve()
        case _:
            print(f"usage: python {THIS} [main.py]", file=sys.stderr)
            sys.exit(2)


def get_server_port():
    """Read the configured Server port, or default to port 8000."""
    value = os.environ.get(SERVER_PORT_ENV, str(PORT)) or str(PORT)
    try:
        port = int(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid {SERVER_PORT_ENV}: {value!r}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"invalid {SERVER_PORT_ENV}: {port} (want 1-65535)")
    return port


def load_server(serve_path):
    """Instantiate the local Modal Server defined by a Python file."""
    # Get serve dir and file.
    serve_path = Path(serve_path)
    if not serve_path.is_file():
        raise FileNotFoundError(f"Server file does not exist: {serve_path}")
    serve_dir = str(serve_path.parent)
    os.chdir(serve_dir)
    sys.path.insert(0, serve_dir)
    # Get namespace of module.
    namespace = runpy.run_path(str(serve_path))
    # Deduplicate Server handle identities at the module-level.
    module_name = namespace["__name__"]
    handles = {}
    for name, value in namespace.items():
        if not isinstance(value, modal.Server):
            continue
        try:
            user_cls = value._get_user_cls()
        except (AssertionError, AttributeError):
            continue
        # Ignore Servers defined in imported modules.
        if user_cls.__module__ != module_name:
            continue
        if id(value) in handles:
            handles[id(value)][1].append(name)
        else:
            handles[id(value)] = (value, [name])
    # Expect a single handle to a Modal Server defined in this module.
    if not handles:
        raise RuntimeError(f"{serve_path} does not define a Modal Server")
    if len(handles) > 1:
        names = ["/".join(aliases) for _, aliases in handles.values()]
        raise RuntimeError(
            f"{serve_path} defines multiple Modal Servers: {', '.join(names)}"
        )
    server_handle, _ = next(iter(handles.values()))
    server = server_handle._get_user_cls()()
    return server


def lifecycle_hooks(
    server: Any, flag: _PartialFunctionFlags
) -> Mapping[str, Callable[..., Any]]:
    """Return lifecycle hooks in the same order used by Modal's runtime."""
    return _find_callables_for_obj(server, flag)


async def call_lifecycle_hook(server, flag, phase):
    """Run a specific lifecycle hook of a Modal Server."""
    for name, hook in lifecycle_hooks(server, flag).items():
        print(f"running {phase} hook: {type(server).__name__}.{name}", flush=True)
        result = hook()
        if inspect.isawaitable(result):
            await result


def install_signal_handlers(stop_event):
    """Turn termination signals into a graceful lifecycle shutdown."""

    def stop(_signum, _frame):
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)


async def run_server(server, server_port):
    """Run all Server lifecycle phases and wait for a termination signal."""
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)
    started = False
    start_time = time.monotonic()

    try:
        await call_lifecycle_hook(
            server, _PartialFunctionFlags.ENTER_PRE_SNAPSHOT, "pre-snapshot enter"
        )
        await call_lifecycle_hook(
            server, _PartialFunctionFlags.ENTER_POST_SNAPSHOT, "enter"
        )
        started = True
        print(f"server up in {time.monotonic() - start_time:.1f}s", flush=True)
        # Block until the launcher receives SIGINT or SIGTERM.
        await stop_event.wait()
    finally:
        await shutdown(server, started, server_port)
    return 0


async def shutdown(server, started, server_port):
    """Run exit hooks and ensure the Server port is clear."""
    try:
        if started:
            await call_lifecycle_hook(server, _PartialFunctionFlags.EXIT, "exit")
    finally:
        clear_port(server_port)


def listeners(port):
    """PIDs listening on port, read from /proc."""
    inodes = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = table.read_text().splitlines()[1:]
        except FileNotFoundError:
            continue
        for line in lines:
            fields = line.split()
            if fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                inodes.add(fields[9])
    if not inodes:
        return set()

    pids = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) in (1, os.getpid()):
            continue
        pid = int(entry)
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:  # Exited between listing /proc and listing its FDs.
            continue
        for fd in fds:
            try:
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if link.startswith("socket:[") and link[8:-1] in inodes:
                pids.add(pid)
                break
    return pids


def clear_port(port):
    """Kill anything listening on port, so a fresh Server can bind it."""
    pids = listeners(port)
    if not pids:
        return
    print(f"clearing port {port}: killing {sorted(pids)}", flush=True)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    deadline = time.monotonic() + 5  # Sockets can outlive their process briefly.
    while time.monotonic() < deadline and listeners(port):
        time.sleep(0.25)
    if listeners(port):
        print(f"warning: port {port} is still in use", file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
