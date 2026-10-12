"""Link verification for the fact-check pass — the only ground truth it has.

An LLM asked "is this URL real?" answers confidently either way because it has
no way to look. So the gateway fetches each URL and reports what the server
said. Everything downstream treats that as fact and the model's opinion as
opinion.

The browser cannot do this itself: cross-origin requests to arbitrary sites are
blocked by CORS. Running it on the Worker is what makes it possible at all.

Because the URLs come from LLM output, which the topic text and audience
questions can steer, the endpoint is guarded against being used to probe
private networks — see :func:`is_blocked_host`.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from typing import TypedDict
from urllib.parse import urlparse

from workers import fetch


class LinkCheck(TypedDict, total=False):
    """Result of one URL probe; ``reason``/``final_url``/``http_status`` optional."""

    url: str
    verdict: str
    http_status: int
    final_url: str
    reason: str


MAX_URLS = 12
PER_URL_TIMEOUT_S = 6
CONCURRENCY = 6

USER_AGENT = "YuktiAI-LinkCheck/1.0 (link verification for a debate transcript)"

_PRIVATE_HOSTNAME = re.compile(
    r"^(localhost|.*\.localhost|.*\.local|.*\.internal|.*\.home\.arpa|.*\.lan)$",
    re.IGNORECASE,
)


def is_blocked_host(hostname: str) -> str | None:
    """Return a reason string when a host must not be fetched, else None.

    Blocks loopback, private, link-local (cloud metadata at 169.254.169.254) and
    carrier-grade NAT ranges, plus internal-only hostname suffixes.

    Args:
        hostname: Host part of the URL.

    Returns:
        Why the host is blocked, or None when it is fetchable.
    """
    host = hostname.lower().strip("[]")
    if not host:
        return "empty hostname"
    if _PRIVATE_HOSTNAME.match(host):
        return "private hostname"

    v4 = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", host)
    if v4:
        a = int(v4.group(1))
        b = int(v4.group(2))
        if a in (0, 10, 127):
            return "private or loopback address"
        if a == 172 and 16 <= b <= 31:
            return "private address"
        if a == 192 and b == 168:
            return "private address"
        if a == 169 and b == 254:
            return "link-local address"
        if a == 100 and 64 <= b <= 127:
            return "carrier-grade NAT address"
        if a >= 224:
            return "multicast or reserved address"

    if host in ("::1", "::"):
        return "loopback address"
    if re.match(r"^f[cd][0-9a-f]{2}:", host, re.IGNORECASE):
        return "unique-local address"
    if re.match(r"^fe80:", host, re.IGNORECASE):
        return "link-local address"

    return None


def _verdict_for_status(status: int) -> str:
    """Map an HTTP status to a verdict."""
    if 200 <= status < 400:
        return "live"
    if status in (404, 410):
        return "missing"
    if status in (401, 403, 429):
        return "restricted"
    if status >= 500:
        return "server-error"
    return "restricted"


async def _check_one(raw_url: str) -> LinkCheck:
    """Probe a single URL with HEAD, retrying with a ranged GET when refused."""
    if not raw_url.startswith(("http://", "https://")):
        return {"url": raw_url, "verdict": "blocked", "reason": "not a valid URL"}

    parsed = urlparse(raw_url)
    if not parsed.hostname:
        return {"url": raw_url, "verdict": "blocked", "reason": "not a valid URL"}
    if parsed.scheme not in ("http", "https"):
        return {
            "url": raw_url,
            "verdict": "blocked",
            "reason": f"unsupported scheme {parsed.scheme}",
        }

    blocked = is_blocked_host(parsed.hostname)
    if blocked:
        return {"url": raw_url, "verdict": "blocked", "reason": blocked}

    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    try:
        response = await _fetch_with_timeout(raw_url, headers=headers, method="HEAD")
        response = await _maybe_get_retry(raw_url, headers, response)
        return {
            "url": raw_url,
            "verdict": _verdict_for_status(response.status),
            "http_status": response.status,
        }
    except TimeoutError:
        return {"url": raw_url, "verdict": "unreachable", "reason": "no response within 6s"}
    except (OSError, RuntimeError, ValueError, TypeError):
        return {
            "url": raw_url,
            "verdict": "unreachable",
            "reason": "host did not resolve or refused the connection",
        }


async def _fetch_with_timeout(url: str, *, headers: dict[str, str], method: str) -> object:
    """Wrapper around the Worker fetch with a hard per-URL timeout."""
    return await asyncio.wait_for(
        fetch(url, method=method, headers=headers), timeout=PER_URL_TIMEOUT_S
    )


async def _maybe_get_retry(url: str, headers: dict[str, str], head_response: object) -> object:
    """Retry with a GET when the server refuses HEAD; never reads the body."""
    status = getattr(head_response, "status", 0)
    if status not in (400, 403, 405, 501):
        await _cancel_body(head_response)
        return head_response

    try:
        retry = await _fetch_with_timeout(
            url, headers={**headers, "Range": "bytes=0-0"}, method="GET"
        )
    except (OSError, RuntimeError, ValueError, TypeError, TimeoutError):
        return head_response

    await _cancel_body(retry)
    return retry


async def _cancel_body(response: object) -> None:
    """Release the body stream so the connection can be reused."""
    cancel = getattr(getattr(response, "body", None), "cancel", None)
    if callable(cancel):
        with suppress(OSError, RuntimeError, TypeError, ValueError):
            await cancel()


async def verify_links(urls: list[str]) -> list[LinkCheck]:
    """Check a batch of URLs, capping the count and batching concurrency.

    Args:
        urls: Raw URLs from a transcript turn.

    Returns:
        Checks in the same (deduplicated) order as the input.
    """
    unique: list[str] = []
    for raw_url in urls:
        cleaned = raw_url.strip()
        if not cleaned or cleaned in unique:
            continue
        unique.append(cleaned)
    unique = unique[:MAX_URLS]

    results: list[LinkCheck] = []
    for start in range(0, len(unique), CONCURRENCY):
        batch = unique[start : start + CONCURRENCY]
        checks = await asyncio.gather(*(_check_one(url) for url in batch))
        results.extend(checks)
    return results
