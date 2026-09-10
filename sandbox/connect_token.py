"""Create a Connect Token for a Sandbox.

Creating stores a Sandbox Connect Token
(https://modal.com/docs/guide/sandbox-networking) in a per-Sandbox Modal
Queue, registered in a Dict keyed by Sandbox ID.

Run `python sandbox/connect_token.py sb-...` to add a token, or
`python sandbox/connect_token.py sb-... --connect [PATH]` to GET
from the Sandbox using the latest stored token.
"""

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import modal
from modal.types import SandboxConnectCredentials

from create_sandbox import DEFAULT_SERVER_PORT

TOKENS_DICT_NAME = "agentx-sandbox-tokens"
MAX_USER_METADATA_CHARS = 512


def main(
    sandbox_id: str,
    port: int | None,
    user_metadata: str | dict[str, Any] | None,
    connect_path: str | None,
) -> None:
    """Create and store a Connect Token, or --connect to the Sandbox with the latest one."""
    if connect_path is not None:
        connect(sandbox_id, connect_path)
        return
    port = DEFAULT_SERVER_PORT if port is None else port
    user_metadata = validate_user_metadata(user_metadata)
    sandbox = _get_sandbox(sandbox_id)
    creds = create_token(sandbox, user_metadata, port)
    store_token(_get_token_dict(), sandbox_id, creds, port)
    _print_usage(sandbox_id, creds)


def create_token(
    sandbox: modal.Sandbox,
    user_metadata: str | dict[str, Any] | None,
    port: int,
) -> SandboxConnectCredentials:
    """Create a Connect Token routing requests to the given container port."""
    return sandbox.create_connect_token(user_metadata=user_metadata, port=port)


def connect(sandbox_id: str, path: str = "/") -> None:
    """GET path from the Sandbox's Connect URL using its latest stored token.

    Stdlib-only reference for consuming a Connect Token: read the newest
    entry off the token Queue, then send one request with the token as a
    bearer credential.
    """

    token_dict = _get_token_dict()
    if not token_dict.contains(sandbox_id):
        raise SystemExit(
            f"error: no tokens stored for Sandbox {sandbox_id}. "
            "run this script without --connect to create one"
        )

    tokens = list(token_dict[sandbox_id].iterate(item_poll_timeout=0))
    if not tokens:
        raise SystemExit(
            f"error: no tokens stored for Sandbox {sandbox_id}. "
            "run this script without --connect to create one"
        )

    token = tokens[-1]
    url = token["url"] + (path if path.startswith("/") else f"/{path}")
    print(f"GET {url} (token created {token['created_at']})")
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token['token']}"}
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, body = f"{response.status} {response.reason}", response.read()
    except urllib.error.HTTPError as e:
        status, body = f"{e.code} {e.reason}", e.read()
    except urllib.error.URLError as e:
        raise SystemExit(f"error: request failed: {e.reason}") from e

    text = body.decode(errors="replace")
    if len(text) > 2000:
        text = text[:2000] + "\n... (truncated)"

    print("Response:", status, sep="\n\t")
    print("Body:", text, sep="\n\t")


def _get_sandbox(sandbox_id: str) -> modal.Sandbox:
    """Find the running Sandbox."""
    try:
        sandbox = modal.Sandbox.from_id(sandbox_id)
        exit_code = sandbox.poll()
    except Exception as e:
        raise SystemExit(f"error: no running Sandbox {sandbox_id!r}: {e}") from e
    if exit_code is not None:
        raise SystemExit(
            f"error: Sandbox {sandbox_id} has finished (exit code {exit_code})"
        )
    return sandbox


def validate_user_metadata(
    user_metadata: str | dict[str, Any] | None,
) -> str | dict[str, Any] | None:
    """Check user_metadata against the Connect Token constraints."""
    if user_metadata is None:
        return None
    if isinstance(user_metadata, str):
        serialized = user_metadata
    elif isinstance(user_metadata, dict):
        try:
            serialized = json.dumps(user_metadata)
        except (TypeError, ValueError) as e:
            raise ValueError(f"user_metadata must be JSON-serializable: {e}") from e
    else:
        raise ValueError(
            f"user_metadata must be a str, dict, or None, got {type(user_metadata).__name__}"
        )
    if len(serialized) >= MAX_USER_METADATA_CHARS:
        raise ValueError(
            f"user_metadata must be less than {MAX_USER_METADATA_CHARS} characters "
            f"after serialization, got {len(serialized)}"
        )
    return user_metadata


def _get_token_dict() -> modal.Dict:
    """Look up the persistent Dict mapping Sandbox ID to its tokens."""
    return modal.Dict.from_name(TOKENS_DICT_NAME, create_if_missing=True)


def _get_token_queue(token_dict: modal.Dict, sandbox_id: str) -> modal.Queue:
    """Return the Queue holding the Sandbox's Connect Tokens, creating it once."""
    if token_dict.contains(sandbox_id):
        return token_dict[sandbox_id]
    queue = modal.Queue.from_name(
        f"{TOKENS_DICT_NAME}-{sandbox_id}", create_if_missing=True
    )
    queue.hydrate()  # resolve the lazy handle; unhydrated objects can't be Dict values
    token_dict[sandbox_id] = queue

    return queue


def store_token(
    token_dict: modal.Dict,
    sandbox_id: str,
    creds: SandboxConnectCredentials,
    port: int,
) -> None:
    """Put the token on the Sandbox's associated Queue.

    Queue entries expire after ~24 hours, which is at least as long as the Sandbox lifetime.

    Tokens can be listed non-destructively or popped.
    """
    entry = {
        "url": creds.url,
        "token": creds.token,
        "port": port,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _get_token_queue(token_dict, sandbox_id).put(entry)


def _print_usage(sandbox_id: str, creds: SandboxConnectCredentials) -> None:
    """Print usage instructions."""
    print(
        "Retrieve token and test connection:",
        f"python sandbox/connect_token.py {sandbox_id} --connect",
        sep="\n\t",
    )
    print("Connect URL:", creds.url, sep="\n\t")
    print(
        f"Connect Tokens for {sandbox_id}:",
        f"Stored in {TOKENS_DICT_NAME!r} Dict under key {sandbox_id!r}",
        sep="\n\t",
    )
    print(
        "Send requests:",
        f'curl -H "Authorization: Bearer $TOKEN" {creds.url}',
        sep="\n\t",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "sandbox_id",
        help="the Sandbox to create a Connect Token for or connect via token to (sb-...)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"container port to route requests to (default: {DEFAULT_SERVER_PORT})",
    )
    parser.add_argument(
        "--user-metadata",
        default=None,
        metavar="JSON_OR_STRING",
        help="metadata stored with the token and received by the server as "
        "X-Verified-User-Data, for application-layer access control (not confidential).",
    )
    parser.add_argument(
        "--connect",
        nargs="?",
        const="/",
        default=None,
        dest="connect_path",
        metavar="PATH",
        help="instead of creating a token, GET PATH (default: /) from the "
        "Sandbox's Connect URL using the latest stored token",
    )
    args = parser.parse_args()

    if args.connect_path is not None and (
        args.port is not None or args.user_metadata is not None
    ):
        parser.error("--connect cannot be combined with --port or --user-metadata")

    user_metadata: str | dict[str, Any] | None = args.user_metadata
    if isinstance(user_metadata, str):
        try:
            parsed = json.loads(user_metadata)
        except json.JSONDecodeError:
            pass  # keep the raw string as metadata
        else:
            if isinstance(parsed, dict):
                user_metadata = parsed

    main(
        sandbox_id=args.sandbox_id,
        port=args.port,
        user_metadata=user_metadata,
        connect_path=args.connect_path,
    )
