"""
Moonraker API key storage and request header helpers.
"""
import os
from pathlib import Path
from typing import Dict, Optional

from .config import DATA_DIR


DEFAULT_API_KEY_FILE = DATA_DIR / "moonraker-api-key"


class MoonrakerApiKeyError(ValueError):
    """Raised when Moonraker API key settings are invalid."""


def _api_key_file(key_file: Optional[Path] = None) -> Path:
    if key_file is not None:
        return Path(key_file)
    return Path(os.environ.get("RAVENS_PERCH_MOONRAKER_API_KEY_FILE", DEFAULT_API_KEY_FILE))


def _validate_api_key(api_key: str) -> str:
    clean_key = api_key.strip()
    if not clean_key:
        raise MoonrakerApiKeyError("Moonraker API key cannot be empty.")
    if "\n" in clean_key or "\r" in clean_key:
        raise MoonrakerApiKeyError("Moonraker API key cannot contain line breaks.")
    return clean_key


def read_moonraker_api_key(key_file: Optional[Path] = None) -> str:
    """Read the configured Moonraker API key, or return an empty string."""
    path = _api_key_file(key_file)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_moonraker_api_key(api_key: str, key_file: Optional[Path] = None) -> Path:
    """Save a Moonraker API key to a file readable only by the service user."""
    clean_key = _validate_api_key(api_key)
    path = _api_key_file(key_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        file_obj = os.fdopen(fd, "w", encoding="utf-8")
    except Exception:
        os.close(fd)
        raise

    with file_obj:
        file_obj.write(f"{clean_key}\n")

    return path


def clear_moonraker_api_key(key_file: Optional[Path] = None) -> bool:
    """Remove the configured Moonraker API key file if present."""
    path = _api_key_file(key_file)
    if not path.exists():
        return False
    path.unlink()
    return True


def moonraker_api_key_configured(key_file: Optional[Path] = None) -> bool:
    """Return True when a non-empty Moonraker API key is configured."""
    return bool(read_moonraker_api_key(key_file=key_file))


def moonraker_auth_headers(key_file: Optional[Path] = None) -> Dict[str, str]:
    """Build auth headers for Moonraker requests."""
    api_key = read_moonraker_api_key(key_file=key_file)
    return {"X-Api-Key": api_key} if api_key else {}
