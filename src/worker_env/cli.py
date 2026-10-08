"""Command-line tool for managing the worker-env secret vault."""

import argparse
import base64
import json
import os
import sys
from collections.abc import Sequence

import httpx


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the vault CLI.

    Returns:
        The configured parser with secret management subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="vault",
        description="Manage secrets in the Cloudflare Worker vault",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("VAULT_URL"),
        help="Vault base URL (default: $VAULT_URL)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("VAULT_TOKEN"),
        help="Bearer token for authentication (default: $VAULT_TOKEN)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # get <namespace>/<key>
    get_cmd = subparsers.add_parser("get", help="Retrieve a secret")
    get_cmd.add_argument("path", help="Secret path (namespace/key)")

    # set <namespace>/<key> <value>
    set_cmd = subparsers.add_parser("set", help="Create or update a secret")
    set_cmd.add_argument("path", help="Secret path (namespace/key)")
    set_cmd.add_argument("value", nargs="?", help="Secret value (reads from stdin if omitted)")

    # delete <namespace>/<key>
    delete_cmd = subparsers.add_parser("delete", help="Delete a secret")
    delete_cmd.add_argument("path", help="Secret path (namespace/key)")

    # list [namespace]
    list_cmd = subparsers.add_parser("list", help="List secrets")
    list_cmd.add_argument("namespace", nargs="?", help="Optional namespace filter")

    # gentoken
    subparsers.add_parser("gentoken", help="Generate a random bearer token")

    return parser


def _parse_path(path: str) -> tuple[str, str]:
    """Parse a namespace/key path.

    Args:
        path: Path in format "namespace/key".

    Returns:
        Tuple of (namespace, key).

    Raises:
        SystemExit: If the path format is invalid.
    """
    parts = path.split("/", 1)
    if len(parts) != 2:
        raise SystemExit(f"Invalid path '{path}': expected 'namespace/key'")
    return parts[0], parts[1]


def _run_get(url: str, token: str, path: str) -> int:
    """Retrieve and print a secret.

    Args:
        url: Vault base URL.
        token: Bearer token for authentication.
        path: Secret path (namespace/key).

    Returns:
        Process exit code.
    """
    namespace, key = _parse_path(path)
    try:
        response = httpx.get(
            f"{url}/secrets/{namespace}/{key}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        secret = response.json()
        print(secret["value"])
        return 0
    except httpx.HTTPStatusError as exc:
        print(
            f"Error: {exc.response.status_code} - {exc.response.json().get('detail', 'Unknown error')}",
            file=sys.stderr,
        )
        return 1
    except httpx.RequestError as exc:
        print(f"Error: Failed to connect to vault: {exc}", file=sys.stderr)
        return 1


def _run_set(url: str, token: str, path: str, value: str | None) -> int:
    """Create or update a secret.

    Args:
        url: Vault base URL.
        token: Bearer token for authentication.
        path: Secret path (namespace/key).
        value: Secret value (or None to read from stdin).

    Returns:
        Process exit code.
    """
    namespace, key = _parse_path(path)
    actual_value = value if value is not None else sys.stdin.read().strip()

    try:
        response = httpx.put(
            f"{url}/secrets/{namespace}/{key}",
            headers={"Authorization": f"Bearer {token}"},
            json={"value": actual_value},
            timeout=10.0,
        )
        response.raise_for_status()
        secret = response.json()
        print(f"Secret '{namespace}/{key}' updated at {secret['updated_at']}")
        return 0
    except httpx.HTTPStatusError as exc:
        print(
            f"Error: {exc.response.status_code} - {exc.response.json().get('detail', 'Unknown error')}",
            file=sys.stderr,
        )
        return 1
    except httpx.RequestError as exc:
        print(f"Error: Failed to connect to vault: {exc}", file=sys.stderr)
        return 1


def _run_delete(url: str, token: str, path: str) -> int:
    """Delete a secret.

    Args:
        url: Vault base URL.
        token: Bearer token for authentication.
        path: Secret path (namespace/key).

    Returns:
        Process exit code.
    """
    namespace, key = _parse_path(path)
    try:
        response = httpx.delete(
            f"{url}/secrets/{namespace}/{key}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        print(f"Secret '{namespace}/{key}' deleted")
        return 0
    except httpx.HTTPStatusError as exc:
        print(
            f"Error: {exc.response.status_code} - {exc.response.json().get('detail', 'Unknown error')}",
            file=sys.stderr,
        )
        return 1
    except httpx.RequestError as exc:
        print(f"Error: Failed to connect to vault: {exc}", file=sys.stderr)
        return 1


def _run_list(url: str, token: str, namespace: str | None) -> int:
    """List secrets.

    Args:
        url: Vault base URL.
        token: Bearer token for authentication.
        namespace: Optional namespace filter.

    Returns:
        Process exit code.
    """
    params = {"namespace": namespace} if namespace else {}
    try:
        response = httpx.get(
            f"{url}/secrets",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        if data["count"] == 0:
            print("No secrets found")
            return 0

        for secret in data["secrets"]:
            print(f"{secret['namespace']}/{secret['key']:<40} (updated: {secret['updated_at']})")
        print(f"\nTotal: {data['count']} secrets")
        return 0
    except httpx.HTTPStatusError as exc:
        print(
            f"Error: {exc.response.status_code} - {exc.response.json().get('detail', 'Unknown error')}",
            file=sys.stderr,
        )
        return 1
    except httpx.RequestError as exc:
        print(f"Error: Failed to connect to vault: {exc}", file=sys.stderr)
        return 1


def _run_gentoken() -> int:
    """Generate a random bearer token.

    Returns:
        Process exit code.
    """
    token = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    payload = {
        "bearer_token": token,
        "next_steps": [
            "Set VAULT_BEARER_TOKEN in wrangler.toml [vars]",
            "Set VAULT_TOKEN and VAULT_URL in your environment for CLI access",
            "Store the token securely in ~/docker/stack/.env; never commit it",
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the vault management CLI.

    Args:
        argv: Argument list without the program name; defaults to sys.argv.

    Returns:
        Process exit code.
    """
    args = _build_parser().parse_args(argv)

    if args.command == "gentoken":
        return _run_gentoken()

    if not args.url:
        print("Error: --url or $VAULT_URL is required", file=sys.stderr)
        return 1
    if not args.token:
        print("Error: --token or $VAULT_TOKEN is required", file=sys.stderr)
        return 1

    url = args.url.rstrip("/")

    if args.command == "get":
        return _run_get(url, args.token, args.path)
    if args.command == "set":
        return _run_set(url, args.token, args.path, args.value)
    if args.command == "delete":
        return _run_delete(url, args.token, args.path)
    if args.command == "list":
        return _run_list(url, args.token, args.namespace)

    raise SystemExit(f"unknown command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
