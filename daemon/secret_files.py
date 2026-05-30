"""
Helpers for writing local secret files with restrictive permissions.
"""
import os
from pathlib import Path
from typing import Union


PathLike = Union[str, Path]


def write_secret_file(path: PathLike, content: str) -> Path:
    """Write a secret file without ever creating it with broad permissions."""
    secret_path = Path(path)
    secret_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        file_obj = os.fdopen(fd, "w", encoding="utf-8")
    except Exception:
        os.close(fd)
        raise

    with file_obj:
        file_obj.write(content)

    return secret_path
