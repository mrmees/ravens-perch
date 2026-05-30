"""
Shared access token for public snapshot URLs registered with Moonraker.
"""
import hmac
import os
import secrets
import threading
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

DEFAULT_SNAPSHOT_TOKEN_FILE = DATA_DIR / "snapshot-token"
_token_lock = threading.Lock()
_cached_snapshot_tokens = {}


def _token_file(token_file: Optional[Path] = None) -> Path:
    if token_file:
        return Path(token_file)
    return Path(os.environ.get("RAVENS_PERCH_SNAPSHOT_TOKEN_FILE", DEFAULT_SNAPSHOT_TOKEN_FILE))


def get_snapshot_token(token_file: Optional[Path] = None) -> str:
    """Return a persistent token for Moonraker snapshot URLs."""
    path = _token_file(token_file)
    cache_key = str(path)

    with _token_lock:
        if cache_key in _cached_snapshot_tokens:
            return _cached_snapshot_tokens[cache_key]

        try:
            if path.exists():
                token = path.read_text(encoding="utf-8").strip()
                if token:
                    _cached_snapshot_tokens[cache_key] = token
                    return token
        except OSError:
            pass

        token = secrets.token_urlsafe(32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token + "\n", encoding="utf-8")
            path.chmod(0o600)
        except OSError:
            pass

        _cached_snapshot_tokens[cache_key] = token
        return token


def valid_snapshot_token(token: Optional[str], token_file: Optional[Path] = None) -> bool:
    """Return whether a request token matches the persistent snapshot token."""
    if not token:
        return False
    return hmac.compare_digest(token, get_snapshot_token(token_file))
