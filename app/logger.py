"""Centralised logging."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def project_root() -> Path:
    """Folder the app lives in: dist/ dir for the frozen exe, else tubefetch/."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def portable_dir() -> Path | None:
    """A single relocatable folder, enabled by creating PORTABLE.txt next to
    run.py (source) or TubeFetch.exe (packaged), or a portable-data/ dir."""
    root = project_root()
    if (root / "PORTABLE.txt").exists() or (root / "portable-data").is_dir():
        d = root / "portable-data"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return None


def is_portable() -> bool:
    return portable_dir() is not None


def app_data_dir(app_name: str = "TubeFetch") -> Path:
    port = portable_dir()
    if port is not None:
        return port
    if sys.platform.startswith("win"):
        base = Path.home() / "AppData" / "Local" / app_name
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / app_name
    else:
        base = Path.home() / ".config" / app_name.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_download_dir() -> Path:
    port = portable_dir()
    if port is not None:
        d = port / "Downloads"
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path.home() / "Downloads" / "TubeFetch"


def resolve_download_dir(raw: str) -> Path:
    """Absolute paths are used as-is; relative ones resolve inside the project
    folder so the whole setup stays relocatable."""
    p = Path(raw or "").expanduser()
    if not str(p).strip():
        return default_download_dir()
    if p.is_absolute():
        return p
    return project_root() / p


def setup_logging(level: int = logging.INFO) -> Path:
    log_dir = app_data_dir()
    log_file = log_dir / "tubefetch.log"
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)
    return log_file


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
