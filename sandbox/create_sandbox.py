"""Create a detached development Sandbox running your Server.

Run with `python sandbox/create_sandbox.py --serve-dir serve `.
"""

import argparse
import os
import runpy
import signal
import sys
from pathlib import Path

import modal

THIS = Path(__file__).resolve()
HERE = THIS.parent
ROOT = HERE.parent

DEFAULT_SERVE_DIR = "serve"
SERVE_FILE = "main.py"
DEFAULT_SANDBOX_APP_NAME = "agentx-sandboxes"
WORKTREE = "/workspace"
SERVE_FILE_ENV = "MODAL_SANDBOX_SERVE_FILE"

MINUTES = 60
HOURS = 60 * MINUTES
DEFAULT_TTL = 8 * HOURS
DEFAULT_IDLE_TIMEOUT = 2 * HOURS
DEFAULT_SERVER_PORT = 8000


def _run_server() -> int:
    """Run the local Server replica and return its child process's exit code.

    This mode runs inside the Sandbox.
    """
    serve_file = Path(os.environ[SERVE_FILE_ENV])
    serve_dir = str(serve_file.parent)
    os.chdir(serve_dir)
    sys.path.insert(0, serve_dir)
    namespace = runpy.run_path(str(serve_file))

    server_handle = namespace["Server"]
    server = server_handle._get_user_cls()()

    def exit_on_signal(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGINT, exit_on_signal)
    signal.signal(signal.SIGTERM, exit_on_signal)

    started = False
    try:
        server.startup()
        started = True

        process = getattr(server, "proc", None)
        if process is None:
            process = server.endpoint._proc
        assert process is not None, "Server.startup() did not create a child process"
        return int(process.wait() or 0)
    finally:
        if started and hasattr(server, "stop"):
            server.stop()


if __name__ == "__main__" and sys.argv[1:] == ["run-server"]:
    """The Server runner inside the Sandbox."""
    exit_code = _run_server()
    raise SystemExit(exit_code)


def _get_spec(serve_main_path: Path):
    """Resolve the one Modal Server into its resource spec."""
    from modal.cli.shell import _function_spec_from_ref

    assert serve_main_path.is_file(), f"expected {serve_main_path}"
    return _function_spec_from_ref(str(serve_main_path), use_module_mode=False)


def _resolve_serve_paths(
    serve_dir: Path,
) -> tuple[Path, Path]:
    """Resolve the local Serve directory."""
    serve_dir = serve_dir.expanduser()
    if not serve_dir.is_absolute():
        serve_dir = ROOT / serve_dir
    serve_dir = serve_dir.resolve()

    assert serve_dir.is_dir(), f"expected Serve directory: {serve_dir}"

    serve_file = serve_dir / SERVE_FILE
    assert serve_file.is_file(), f"expected Serve file: {serve_file}"
    return serve_dir, serve_file


def _get_environment_name() -> str | None:
    """Return the active Modal Environment, or ``None``."""
    from modal.config import config

    return config.get("environment")


def _append_to_image(
    image: modal.Image,
    serve_dir: Path,
    serve_file: Path,
) -> modal.Image:
    """Mount the Serve directory at the Sandbox worktree."""
    sandbox_serve_file = Path(WORKTREE) / serve_file.relative_to(serve_dir)
    image = image.uv_pip_install("modal==1.5.5")
    if environment_name := _get_environment_name():
        image = image.env({"MODAL_ENVIRONMENT": environment_name})
    image = image.env({SERVE_FILE_ENV: str(sandbox_serve_file)})
    image = image.add_local_file(THIS, "/root/create_sandbox.py", copy=False)
    image = image.add_local_dir(serve_dir, WORKTREE, copy=False)
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
) -> modal.Sandbox:
    """Translate a Server resource spec into a long-lived Sandbox."""
    sandbox_gpus = spec.gpus
    if isinstance(sandbox_gpus, list):
        sandbox_gpus = sandbox_gpus[0] if sandbox_gpus else None

    return modal.Sandbox.create(
        "sleep",
        "infinity",
        app=sandbox_app,
        image=sandbox_image,
        gpu=sandbox_gpus,
        cpu=spec.cpu,
        memory=spec.memory,
        secrets=spec.secrets or [],
        network_file_systems=spec.network_file_systems or {},
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
    )


def _print_access(sb: modal.Sandbox) -> None:
    """Print access and observability for a created Sandbox."""
    print("Dashboard:", sb.get_dashboard_url(), sep="\n\t")
    print("Shell access:", f"modal shell {sb.object_id}", sep="\n\t")
    print("CLI logs:", f"modal container logs {sb.object_id}", sep="\n\t")
    print("Start Server:", "python /root/create_sandbox.py run-server", sep="\n\t")


def _print_tunnels(sb: modal.Sandbox) -> None:
    """Print every public TLS tunnel exposed by the Sandbox."""
    tunnels = sb.tunnels(timeout=2 * MINUTES)
    for port, tunnel in sorted(tunnels.items()):
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
        "--serve-dir",
        type=Path,
        default=Path(DEFAULT_SERVE_DIR),
        help=f"Serve directory, relative to the repository root or absolute (default: {DEFAULT_SERVE_DIR})",
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
        help="Open an additional Modal Tunnel; may be given multiple times.",
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
    args = parser.parse_args()

    sandbox_app_name = args.app_name or DEFAULT_SANDBOX_APP_NAME
    sandbox_app = _get_app(sandbox_app_name)

    serve_dir, serve_file = _resolve_serve_paths(args.serve_dir)
    spec = _get_spec(serve_file)
    sandbox_image = _append_to_image(spec.image, serve_dir, serve_file)

    sb = _create_from_spec(
        spec,
        sandbox_app=sandbox_app,
        sandbox_image=sandbox_image,
        sandbox_ttl=args.ttl,
        sandbox_idle_timeout=args.idle_timeout,
        server_port=args.server_port,
        tunnel_ports=args.tunnel_ports,
    )

    # Print access and observability info, and every public TLS tunnel for the Sandbox.
    _print_access(sb)
    _print_tunnels(sb)


if __name__ == "__main__":
    main()
