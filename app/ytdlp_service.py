"""yt-dlp integration: metadata fetching + format parsing. No UI imports."""
from __future__ import annotations

import time
from typing import Callable

import yt_dlp

from . import ffmpeg_utils
from .logger import get_logger
from .models import PlaylistInfo, PlaylistItem, VideoInfo, parse_raw_formats
from .validators import parse_youtube_url

log = get_logger(__name__)

_FETCH_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": False,
    "extract_flat": False,
    "skip_download": True,
    "socket_timeout": 20,
}


def _base_opts(extra: dict | None = None) -> dict:
    opts = dict(_FETCH_OPTS)
    if extra:
        opts.update(extra)
    return opts


def fetch_media(url: str, max_playlist_items: int = 50) -> VideoInfo | PlaylistInfo:
    """Fetch metadata. Raises RuntimeError with user-friendly text on failure."""
    parsed = parse_youtube_url(url)
    if parsed.kind == "invalid":
        raise ValueError(parsed.reason or "Invalid YouTube URL.")
    target = parsed.normalised or url
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(_base_opts()) as ydl:
                info = ydl.extract_info(target, download=False)
            if not info:
                raise RuntimeError("YouTube returned no information for this link.")
            if info.get("_type") == "playlist" or "entries" in info:
                return _to_playlist(info, target, max_playlist_items)
            return _to_video(info)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as friendly error
            last_err = exc
            log.warning("fetch attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    from .utils import friendly_error

    raise RuntimeError(friendly_error(str(last_err)))


def _to_video(info: dict) -> VideoInfo:
    combined, video_only, audio_only = parse_raw_formats(info.get("formats") or [])
    vid = info.get("id") or ""
    return VideoInfo(
        video_id=vid,
        title=info.get("title") or "Untitled",
        uploader=info.get("uploader") or info.get("channel"),
        duration=info.get("duration"),
        view_count=info.get("view_count"),
        thumbnail=info.get("thumbnail"),
        webpage_url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
        combined=combined,
        video_only=video_only,
        audio_only=audio_only,
    )


def _to_playlist(info: dict, target: str, limit: int) -> PlaylistInfo:
    entries = [e for e in (info.get("entries") or []) if e]
    items: list[PlaylistItem] = []
    for i, e in enumerate(entries[:limit]):
        vid = e.get("id") or ""
        title = e.get("title") or f"Video {i + 1}"
        dur = e.get("duration")
        items.append(PlaylistItem(index=i + 1, video_id=vid, title=title,
                                 url=f"https://www.youtube.com/watch?v={vid}",
                                 duration=dur if isinstance(dur, int) else None))
    return PlaylistInfo(
        title=info.get("title"),
        uploader=info.get("uploader"),
        count=info.get("playlist_count") or len(items),
        items=items,
        webpage_url=target,
    )


def build_ydl_options(req, ffmpeg: str | None, progress_hook: Callable, outtmpl: str) -> dict:
    """Build yt-dlp options for an actual download."""
    from .models import build_format_spec, merge_output_format

    fmt, postprocessors = build_format_spec(req)
    merge_fmt = merge_output_format(req)
    opts: dict = {
        "format": fmt,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
        "continuedl": True,
        "nopart": False,
        "progress_hooks": [progress_hook],
        "postprocessors": list(postprocessors),
        "prefer_ffmpeg": True,
        "restrictfilenames": False,
    }
    if merge_fmt and req.mode in ("video_audio", "video_only"):
        opts["merge_output_format"] = merge_fmt
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if req.mode == "audio_only":
        # keep metadata + thumbnail best-effort
        opts["writethumbnail"] = False
        opts["addmetadata"] = True
    return opts


def check_runtime_deps(ffmpeg_path: str = "") -> tuple[str | None, str]:
    ff = ffmpeg_utils.find_ffmpeg(ffmpeg_path)
    if ff:
        ver = ffmpeg_utils.ffmpeg_version(ff) or "FFmpeg available"
        return ff, ver
    return None, "FFmpeg not found — video+audio merging and MP3 conversion need it."
