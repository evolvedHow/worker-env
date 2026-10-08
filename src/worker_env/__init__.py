"""worker-env: a secure, Cloudflare-backed context vault."""

from importlib.metadata import PackageNotFoundError, version

from worker_env.models import Context, ContextCreate, ContextList, ContextUpdate
from worker_env.signing import KeyPair, generate_keypair

try:
    __version__ = version("worker-env")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "Context",
    "ContextCreate",
    "ContextList",
    "ContextUpdate",
    "KeyPair",
    "__version__",
    "generate_keypair",
]
