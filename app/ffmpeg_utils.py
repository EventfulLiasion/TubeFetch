"""FFmpeg discovery + thin wrappers around mux/convert."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)


def find_ffmpeg(preferred: str = "") -> str | None:
    """Locate a usable ffmpeg binary. Order: explicit path, PATH, imageio-ffmpeg."""
    if preferred:
        p = Path(preferred)
        if p.is_file():
            return str(p)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(exe).is_file():
            return str(exe)
    except Exception as exc:
        log.debug("imageio-ffmpeg not available: %s", exc)
    return None


def ffmpeg_version(ffmpeg: str) -> str | None:
    try:
        out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10)
        first = (out.stdout or "").splitlines()
        return first[0].strip() if first else None
    except Exception:
        return None


def mux_video_audio(ffmpeg: str, video: Path, audio: Path, output: Path) -> None:
    """Mux separate streams without re-encoding (fast, lossless)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-i", str(video), "-i", str(audio),
           "-c", "copy", "-map", "0:v:0", "-map", "1:a:0",
           "-movflags", "+faststart", str(output)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg mux failed: {(proc.stderr or proc.stdout)[-2000:]}")
