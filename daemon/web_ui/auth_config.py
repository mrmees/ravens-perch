"""
Dynamic web authentication configuration.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ..config import DATA_DIR
from ..secret_files import write_secret_file

DEFAULT_USERNAME = "ravens"
DEFAULT_AUTH_ENV_FILE = DATA_DIR / "web-auth.env"
DEFAULT_PASSWORD_FILE = DATA_DIR / "web-ui-password"
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_TRUE_VALUES = {"1", "true", "yes", "on"}


class WebAuthConfigError(ValueError):
    """Raised when web authentication settings are invalid."""


@dataclass(frozen=True)
class WebAuthConfig:
    enabled: bool
    username: str
    password: Optional[str]
    password_file: Path
    config_file: Path
    disabled: bool = False

    @property
    def password_configured(self) -> bool:
        return bool(self.password)


def _auth_env_file(config_file: Optional[Path] = None) -> Path:
    if config_file:
        return Path(config_file)
    return Path(os.environ.get("RAVENS_PERCH_WEB_AUTH_ENV_FILE", DEFAULT_AUTH_ENV_FILE))


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _unquote(value)

    return values


def _read_secret_file(path: Path) -> Optional[str]:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return None


def _is_true(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


def valid_web_auth_username(username: str) -> bool:
    return bool(username and _USERNAME_PATTERN.fullmatch(username))


def _validate_credentials(username: str, password: str) -> None:
    if not valid_web_auth_username(username):
        raise WebAuthConfigError("Username may only contain letters, numbers, dots, underscores, or dashes.")
    if not password:
        raise WebAuthConfigError("Password cannot be empty.")
    if "\n" in password or "\r" in password:
        raise WebAuthConfigError("Password cannot contain line breaks.")


def load_web_auth_config(config_file: Optional[Path] = None) -> WebAuthConfig:
    auth_env_file = _auth_env_file(config_file)
    file_values = _read_env_file(auth_env_file)
    values = file_values if auth_env_file.exists() else os.environ

    disabled = _is_true(values.get("RAVENS_PERCH_WEB_AUTH_DISABLED"))
    username = values.get("RAVENS_PERCH_WEB_AUTH_USERNAME") or DEFAULT_USERNAME
    password_file = Path(values.get("RAVENS_PERCH_WEB_AUTH_PASSWORD_FILE") or DEFAULT_PASSWORD_FILE)

    password = None
    if not disabled:
        password = values.get("RAVENS_PERCH_WEB_AUTH_PASSWORD") or _read_secret_file(password_file)

    return WebAuthConfig(
        enabled=not disabled and bool(password),
        username=username,
        password=password,
        password_file=password_file,
        config_file=auth_env_file,
        disabled=disabled,
    )


def save_web_auth_credentials(
    username: str,
    password: str,
    *,
    config_file: Optional[Path] = None,
    password_file: Optional[Path] = None,
) -> WebAuthConfig:
    _validate_credentials(username, password)

    auth_env_file = _auth_env_file(config_file)
    web_password_file = Path(password_file) if password_file else auth_env_file.parent / DEFAULT_PASSWORD_FILE.name

    write_secret_file(web_password_file, password + "\n")

    write_secret_file(
        auth_env_file,
        "\n".join(
            [
                f"RAVENS_PERCH_WEB_AUTH_USERNAME={username}",
                f"RAVENS_PERCH_WEB_AUTH_PASSWORD_FILE={web_password_file}",
                "",
            ]
        ),
    )

    return load_web_auth_config(config_file=auth_env_file)


def web_auth_status() -> Dict[str, object]:
    config = load_web_auth_config()
    return {
        "enabled": config.enabled,
        "username": config.username,
        "password_configured": config.password_configured,
        "password_file": str(config.password_file),
    }
