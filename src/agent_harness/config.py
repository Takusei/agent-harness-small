"""Load only the current directory's .env; explicit local values win over the shell."""

import os
from pathlib import Path

from dotenv import dotenv_values


def settings(path: Path = Path(".env")) -> dict[str, str]:
    # No interpolation: literal credentials and shell-like text stay literal.
    local = dotenv_values(path, interpolate=False) if path.is_file() else {}
    return {**os.environ, **{key: value or "" for key, value in local.items()}}
