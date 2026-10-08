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

    # get <context>/<key> or <key>
    get_cmd = subparsers.add_parser("get", help="Retrieve a secret")
    get_cmd.add_argument("path", help="Secret path (context/key or just key for default)")

    # list [context]
    list_cmd = subparsers.add_parser("list", help="List secrets")
    list_cmd.add_argument("context", nargs="?", help="Optional context filter")

    # gentoken
    subparsers.add_parser("gentoken", help="Generate a random bearer token")

    return parser


def _parse_path(path: str) -> tuple[str | None, str]:
    """Parse a context/key path.

    Args:
        path: Path in format "context/key" or just "key" (for default context).

    Returns:
        Tuple of (context, key). Context is None if only key is provided.

    Raises:
        SystemExit: If the path format is invalid.
    """
    parts = path.split("/", 1)
    if len(parts) == 1:
        # Just a key, use default context
        return None, parts[0]
    if len(parts) == 2:
        # context/key format
        return parts[0], parts[1]
    raise SystemExit(f"Invalid path '{path}': expected 'context/key' or 'key'")


def _run_get(url: str, token: str, path: str) -> int:
    """Retrieve and print a secret.

    Args:
        url: Vault base URL.
        token: Bearer token for authentication.
        path: Secret path (context/key or just key for default).

    Returns:
        Process exit code.
    """
    context, key = _parse_path(path)
    try:
        endpoint = f"{url}/secrets/{context}/{key}" if context else f"{url}/secrets/{key}"

        response = httpx.get(
            endpoint,
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


def _run_list(url: str, token: str, context: str | None) -> int:
    """List secrets.

    Args:
        url: Vault base URL.
        token: Bearer token for authentication.
        context: Optional context filter.

    Returns:
        Process exit code.
    """
    params = {"context": context} if context else {}
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

        rows: list[tuple[str, str, str, int]] = []
        for secret in data["secrets"]:
            name = f"{secret['context']}/{secret['key']}"
            label = secret.get("label", "")
            rows.append((name, label, secret["updated_at"], secret.get("update_count", 0)))

        width = max((len(name) for name, *_ in rows), default=0)
        for name, label, updated, update_count in rows:
            label_display = f" - {label}" if label else ""
            print(f"{name:<{width}}{label_display}")
            print(f"  └─ updated: {updated} (count: {update_count})")

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
            "Production: npx wrangler secret put VAULT_BEARER_TOKEN",
            'Local dev: echo "VAULT_BEARER_TOKEN=<token>" > .dev.vars',
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
    if args.command == "list":
        return _run_list(url, args.token, args.context)

    raise SystemExit(f"unknown command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
