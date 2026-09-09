"""Create a detached development Sandbox running your Server.

Expected repository layout:

    serve/
      main.py # defines one Modal Server named Server
    sandbox/
      create_sandbox.py

Run with `python sandbox/create_sandbox.py`.
"""

import argparse
import json
import os
import runpy
import signal
import sys
from pathlib import Path

import modal

THIS = Path(__file__).resolve()
HERE = THIS.parent
ROOT = HERE.parent
# TODO: make this an argument. Assume it contains a main.py at the top level.
# Assumes that python main.py
LOCAL_SERVE_DIR = ROOT / "serve"
STAGED_SERVE_DIR = Path("/mnt/serve")
SERVE_DIR = (
    STAGED_SERVE_DIR if (STAGED_SERVE_DIR / "main.py").is_file() else LOCAL_SERVE_DIR
)
SERVE_MAIN = SERVE_DIR / "main.py"

DEFAULT_SANDBOX_APP_NAME = "agentx-sandboxes"
WORKTREE = "/workspace/serve"
# TODO: no more since not auto running server.
READY_FILE = "/tmp/sandbox-ready"

MINUTES = 60
HOURS = 60 * MINUTES
DEFAULT_TTL = 8 * HOURS
DEFAULT_IDLE_TIMEOUT = 2 * HOURS
DEFAULT_SERVER_PORT = 8000


def _run_server() -> int:
    """Run the local Server replica and return its child process's exit code.

    This mode runs inside the Sandbox.
    """
    serve_main = Path(WORKTREE) / "main.py"
    os.chdir(WORKTREE)
    sys.path.insert(0, WORKTREE)
    namespace = runpy.run_path(str(serve_main))

    server_handle = namespace["Server"]
    server = server_handle._get_user_cls()()

    def stop(_signum, _frame):
        if hasattr(server, "stop"):
            server.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    server.startup()

    process = getattr(server, "proc", None)
    if process is None:
        process = server.endpoint._proc
    assert process is not None, "Server.startup() did not create a child process"
    return int(process.wait() or 0)


# TODO: make this a command you run inside imperatively (not automatically/on init).
if __name__ == "__main__" and sys.argv[1:] == ["run-server"]:
    """The Server runner inside the Sandbox."""
    exit_code = _run_server()
    raise SystemExit(exit_code)


def _get_spec(serve_main_path: Path):
    """Resolve the one Modal Server into its resource spec."""
    from modal.cli.shell import _function_spec_from_ref

    assert serve_main_path.is_file(), f"expected {serve_main_path}"
    return _function_spec_from_ref(str(serve_main_path), use_module_mode=False)


def _get_environment_name() -> str | None:
    """Return the active Modal Environment, or ``None``."""
    from modal.config import config

    return config.get("environment")


def _append_to_image(image: modal.Image) -> modal.Image:
    """Add the Sandbox launcher, ``serve/`` source, modal SDK, and Environment."""
    image = image.uv_pip_install("modal==1.5.5")
    if environment_name := _get_environment_name():
        image = image.env({"MODAL_ENVIRONMENT": environment_name})
    image = image.add_local_file(THIS, "/root/create_sandbox.py", copy=False)
    image = image.add_local_dir(SERVE_DIR, str(STAGED_SERVE_DIR), copy=False)
    return image


def _get_app(app_name: str | None = None) -> modal.App:
    """Look up the App owning all created Sandboxes."""
    return modal.App.lookup(
        app_name or DEFAULT_SANDBOX_APP_NAME,
        create_if_missing=True,
    )


def _create_from_spec(
    spec,
    *,
    sandbox_app: modal.App,
    sandbox_image: modal.Image,
    sandbox_ttl: int,
    sandbox_idle_timeout: int,
    server_port: int,
    tunnel_ports: list[int],
    enable_exit_snapshot: bool,
) -> modal.Sandbox:
    """Translate a Server resource spec into a long-lived Sandbox."""
    sandbox_gpus = spec.gpus
    if isinstance(sandbox_gpus, list):
        sandbox_gpus = sandbox_gpus[0] if sandbox_gpus else None

    bootstrap = (
        "set -eu; "
        # Reset readiness.
        f"rm -f {READY_FILE}; "
        "mkdir -p /workspace; "
        f"if test ! -d {WORKTREE}; then cp -a {STAGED_SERVE_DIR} {WORKTREE}; fi; "
        # Copying done, so ready.
        f"touch {READY_FILE}; "
        # Treat the server as a long-lived process.
        "exec sleep infinity"
    )

    return modal.Sandbox.create(
        "bash",
        "-lc",
        bootstrap,
        app=sandbox_app,
        image=sandbox_image,
        gpu=sandbox_gpus,
        cpu=spec.cpu,
        memory=spec.memory,
        secrets=spec.secrets or [],
        volumes=spec.volumes or {},
        cloud=spec.cloud,
        region=(
            list(spec.scheduler_placement.regions) if spec.scheduler_placement else None
        ),
        proxy=spec.proxy,
        timeout=sandbox_ttl,
        idle_timeout=sandbox_idle_timeout,
        workdir="/root",
        encrypted_ports=list(dict.fromkeys([server_port, *tunnel_ports])),
        readiness_probe=modal.Probe.with_exec("test", "-f", READY_FILE),
        experimental_options={"enable_exit_snapshot": enable_exit_snapshot},
    )


def _print_access(sb: modal.Sandbox) -> None:
    """Print access and observability for a created Sandbox."""
    print("Dashboard:", sb.get_dashboard_url(), sep="\n\t")
    print("Shell access:", f"modal shell {sb.object_id}", sep="\n\t")
    print("CLI logs:", f"modal container logs {sb.object_id}", sep="\n\t")


def _print_tunnels(sb: modal.Sandbox) -> None:
    """Print every public TLS tunnel exposed by the Sandbox."""
    for port, tunnel in sorted(sb.tunnels(timeout=2 * MINUTES).items()):
        print(f"Tunnel for port {port}:", tunnel.url, sep="\n\t")


def main() -> None:
    """Parse local CLI options, create a detached Sandbox, and print its access data."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help=f"Modal App to create the Sandbox in (default: {DEFAULT_SANDBOX_APP_NAME})",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=DEFAULT_SERVER_PORT,
        help=f"Server port to expose (default: {DEFAULT_SERVER_PORT})",
    )
    parser.add_argument(
        "--tunnel",
        action="append",
        type=int,
        default=[],
        dest="tunnel_ports",
        metavar="PORT",
        help="Open a Modal Tunnel to the Server port on the Sandbox.",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=DEFAULT_TTL,
        metavar="SECONDS",
        help=f"seconds before the Sandbox shuts down (default: {DEFAULT_TTL})",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=DEFAULT_IDLE_TIMEOUT,
        metavar="SECONDS",
        help=f"seconds before an idle Sandbox shuts down (default: {DEFAULT_IDLE_TIMEOUT})",
    )
    parser.add_argument(
        "--resume-image-id",
        default="",
        metavar="IMAGE_ID",
        help="resume the filesystem from an exit-snapshot Image",
    )
    parser.add_argument(
        "--exit-snapshot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="enable an exit snapshot (default: enabled)",
    )
    args = parser.parse_args()

    sandbox_app_name = args.app_name or DEFAULT_SANDBOX_APP_NAME
    sandbox_app = _get_app(sandbox_app_name)
    spec = _get_spec(SERVE_MAIN)

    # Resume from Image ID if passed.
    base_image = (
        modal.Image.from_id(args.resume_image_id)
        if args.resume_image_id
        else spec.image
    )
    sandbox_image = _append_to_image(base_image)
    sb = _create_from_spec(
        spec,
        sandbox_app=sandbox_app,
        sandbox_image=sandbox_image,
        sandbox_ttl=args.ttl,
        sandbox_idle_timeout=args.idle_timeout,
        server_port=args.server_port,
        tunnel_ports=args.tunnel_ports,
        enable_exit_snapshot=args.exit_snapshot,
    )

    sb.wait_until_ready()

    # Print access and observability info, and every public TLS tunnel for the Sandbox.
    tunnels = sb.tunnels(timeout=2 * MINUTES)
    tunnel = tunnels[args.server_port]
    _print_access(sb)
    _print_tunnels(sb)


if __name__ == "__main__":
    main()
