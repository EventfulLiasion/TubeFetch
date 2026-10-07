"""Filesystem + formatting helpers (pure, tested)."""
from __future__ import annotations

import re
from pathlib import Path

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, max_len: int = 180) -> str:
    """Make a string safe to use as a file name on Win/Mac/Linux."""
    name = (name or "untitled").strip()
    name = _INVALID_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "untitled"
    if name.upper() in _WINDOWS_RESERVED:
        name = "_" + name
    if len(name) > max_len:
        # keep extension-aware truncation to callers; here just cut
        name = name[:max_len].rstrip(" .")
    return name or "untitled"


def unique_path(directory: Path | str, stem: str, suffix: str) -> Path:
    """Return a non-colliding path: `stem.suffix`, `stem (1).suffix`, ..."""
    directory = Path(directory)
    stem = sanitize_filename(stem)
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        candidate = directory / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def format_bytes(n: float | int | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def format_speed(bps: float | None) -> str:
    if not bps:
        return "—"
    return f"{format_bytes(bps)}/s"


def format_eta(secs: int | float | None) -> str:
    if secs is None:
        return "—"
    secs = int(secs)
    if secs < 0:
        return "—"
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def format_duration(total_seconds: int | float | None) -> str:
    if total_seconds is None:
        return "—"
    s = int(total_seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def friendly_error(msg: str) -> str:
    """Map technical yt-dlp errors to user-friendly messages."""
    low = (msg or "").lower()
    if "video unavailable" in low or "private video" in low:
        return "This video is unavailable or private."
    if "age" in low and ("confirm" in low or "sign in" in low):
        return "Age-restricted video — sign-in required. Try another video."
    if "sign in to confirm" in low or "not a bot" in low:
        return "YouTube is rate-limiting this network. Wait a few minutes and retry."
    if "http error 403" in low:
        return "YouTube refused the download (HTTP 403). Retry — this is usually transient."
    if "http error 429" in low:
        return "Too many requests (HTTP 429). Wait a bit and retry."
    if "name resolution" in low or "temporary failure" in low or "timed out" in low or "connection" in low:
        return "Network problem — check your connection and retry."
    if "unsupported url" in low:
        return "That link is not supported. Paste a youtube.com or youtu.be video/playlist link."
    if "playlist" in low and "empty" in low:
        return "Playlist is empty or unavailable."
    text = (msg or "Download failed.").strip()
    # trim very long tracebacks
    if len(text) > 400:
        text = text[:400] + "…"
    return text[0].upper() + text[1:] if text else "Download failed."
