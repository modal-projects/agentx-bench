"""Create a detached development Sandbox running your Server.

Run with `python sandbox/create_sandbox.py --serve-dir serve `.
"""

import argparse
from pathlib import Path

import modal

THIS = Path(__file__).resolve()
HERE = THIS.parent
ROOT = HERE.parent
START_FILE = HERE / "start.py"

DEFAULT_SERVE_DIR = "serve"
SERVE_FILE = "main.py"
DEFAULT_SANDBOX_APP_NAME = "agentx-sandboxes"
WORKTREE = "/workspace"
SERVE_FILE_ENV = "MODAL_SANDBOX_SERVE_FILE"
SERVER_PORT_ENV = "MODAL_SANDBOX_SERVER_PORT"

MINUTES = 60
HOURS = 60 * MINUTES
DEFAULT_TTL = 8 * HOURS
DEFAULT_IDLE_TIMEOUT = 2 * HOURS


def main(**kwargs):
    serve_dir, serve_file = _resolve_serve_paths(kwargs.get("serve_dir"))
    spec = _get_spec(serve_file)
    dangerously_exposed_port = kwargs.get("dangerously_expose_port", None)

    sandbox_app_name = kwargs.get("app_name") or DEFAULT_SANDBOX_APP_NAME
    sandbox_app = _get_app(sandbox_app_name)

    sandbox_image = _append_to_image(
        spec.image,
        serve_dir,
        serve_file,
        server_port=dangerously_exposed_port,
    )
    sandbox_ttl = kwargs.get("ttl")
    sandbox_idle_timeout = kwargs.get("idle_timeout")
    tunnel_ports = kwargs.get("tunnel_ports") or []

    sb = _create_from_spec(
        spec,
        sandbox_app=sandbox_app,
        sandbox_image=sandbox_image,
        sandbox_ttl=sandbox_ttl,
        sandbox_idle_timeout=sandbox_idle_timeout,
        dangerously_exposed_port=dangerously_exposed_port,
        tunnel_ports=tunnel_ports,
    )

    # Print access and observability info, and every public TLS tunnel for the Sandbox.
    _print_access(sb)
    _print_tunnels(sb)

    return sb.object_id


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


def _append_to_image(
    image: modal.Image,
    serve_dir: Path,
    serve_file: Path,
    server_port: int | None,
) -> modal.Image:
    """Mount the Serve directory at the Sandbox worktree."""
    sandbox_serve_file = Path(WORKTREE) / serve_file.relative_to(serve_dir)
    image = image.uv_pip_install("modal==1.5.5")
    image = image.env(
        {
            SERVE_FILE_ENV: str(sandbox_serve_file),
            SERVER_PORT_ENV: str(server_port) if server_port is not None else "",
        }
    )
    image = image.add_local_file(START_FILE, "/root/start.py", copy=False)
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
    dangerously_exposed_port: int | None,
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
        encrypted_ports=list(
            dict.fromkeys(
                [
                    *(
                        [dangerously_exposed_port]
                        if dangerously_exposed_port is not None
                        else []
                    ),
                    *tunnel_ports,
                ]
            )
        ),
    )


def _print_access(sb: modal.Sandbox) -> None:
    """Print access and observability for a created Sandbox."""
    print("Dashboard:", sb.get_dashboard_url(), sep="\n\t")
    print("Shell access:", f"modal shell {sb.object_id}", sep="\n\t")
    print("Start Server:", "python start.py", sep="\n\t")


def _print_tunnels(sb: modal.Sandbox) -> None:
    """Print every public TLS tunnel exposed by the Sandbox."""
    tunnels = sb.tunnels(timeout=2 * MINUTES)
    for port, tunnel in sorted(tunnels.items()):
        print(f"Tunnel for port {port}:", tunnel.url, sep="\n\t")


def cli():
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
        "--dangerously-expose-port",
        type=int,
        default=None,
        metavar="PORT",
        help="create a public Modal Tunnel exposing the provided port",
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

    kwargs = vars(parser.parse_args())
    main(**kwargs)


if __name__ == "__main__":
    cli()
