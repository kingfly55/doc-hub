from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv


def _global_env_path() -> Path:
    env_override = os.environ.get("DOC_HUB_DATA_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve() / "env"

    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data).expanduser().resolve() / "doc-hub" / "env"

    return Path.home() / ".local" / "share" / "doc-hub" / "env"


def bootstrap_cli(*, default_level: int = logging.INFO) -> None:
    load_dotenv()
    load_dotenv(dotenv_path=_global_env_path())
    level = logging.DEBUG if os.environ.get("LOGLEVEL") == "DEBUG" else default_level
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
