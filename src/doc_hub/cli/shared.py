from __future__ import annotations

import logging
import os

from dotenv import load_dotenv


def bootstrap_cli(*, default_level: int = logging.INFO) -> None:
    load_dotenv()
    level = logging.DEBUG if os.environ.get("LOGLEVEL") == "DEBUG" else default_level
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
