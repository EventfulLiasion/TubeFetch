"""Persistent user preferences (JSON in per-user app-data dir)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .logger import app_data_dir, default_download_dir, get_logger, resolve_download_dir

log = get_logger(__name__)


@dataclass
class AppConfig:
    download_dir: str = ""
    mode: str = "video_audio"          # video_audio | video_only | audio_only
    quality: str = "best"              # format_id or best
    video_format: str = "mp4"
    audio_format: str = "mp3"
    max_retries: int = 3
    max_playlist_items: int = 50
    concurrent_fragments: int = 4
    ffmpeg_path: str = ""              # empty = auto-detect
    remember_history: bool = True

    def __post_init__(self) -> None:
        if not self.download_dir:
            self.download_dir = str(default_download_dir())


class ConfigManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "settings.json")
        self.config = AppConfig()
        self.load()

    def load(self) -> AppConfig:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(self.config, k):
                        setattr(self.config, k, v)
        except Exception as exc:  # corrupt file -> keep defaults
            log.warning("Could not load settings (%s); using defaults.", exc)
        # Relative download dirs resolve inside the project folder, so a
        # portable setup keeps working after being moved to a new location.
        self.config.download_dir = str(resolve_download_dir(self.config.download_dir))
        Path(self.config.download_dir).mkdir(parents=True, exist_ok=True)
        return self.config

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(asdict(self.config), indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Could not save settings: %s", exc)


class HistoryStore:
    """Simple JSON download-history log."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "history.json")
        self.entries: list[dict] = []
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.entries = data[-200:]
        except Exception:
            self.entries = []

    def add(self, entry: dict) -> None:
        self.entries.append(entry)
        self.entries = self.entries[-200:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        except Exception:
            pass

    def clear(self) -> None:
        self.entries = []
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass
