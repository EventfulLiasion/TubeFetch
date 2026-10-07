"""YouTube URL validation / normalisation. No network calls here."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class ParsedUrl:
    kind: str  # "video" | "playlist" | "invalid"
    video_id: str | None = None
    list_id: str | None = None
    normalised: str | None = None
    reason: str | None = None


def _ensure_scheme(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        return "https://" + url
    return url


def parse_youtube_url(raw: str) -> ParsedUrl:
    """Parse without network. Accepts watch, youtu.be, shorts, live, embed, playlist."""
    if not raw or not raw.strip():
        return ParsedUrl(kind="invalid", reason="URL is empty.")
    url = _ensure_scheme(raw.strip())
    try:
        parts = urlparse(url)
    except Exception:
        return ParsedUrl(kind="invalid", reason="Malformed URL.")
    host = (parts.hostname or "").lower()
    if host not in _YT_HOSTS and not host.endswith(".youtube.com"):
        return ParsedUrl(kind="invalid", reason="Not a YouTube URL.")
    path = parts.path or "/"
    qs = parse_qs(parts.query)

    # Short link: youtu.be/<id>
    if host in ("youtu.be", "www.youtu.be"):
        vid = path.strip("/").split("/")[0] if path.strip("/") else ""
        lst = (qs.get("list", [None])[0])
        if vid and _VIDEO_ID_RE.match(vid):
            norm = f"https://www.youtube.com/watch?v={vid}" + (f"&list={lst}" if lst else "")
            if lst:
                return ParsedUrl(kind="playlist", video_id=vid, list_id=lst, normalised=norm)
            return ParsedUrl(kind="video", video_id=vid, normalised=norm)
        return ParsedUrl(kind="invalid", reason="Could not find a video ID in this link.")

    # Playlist-only URL
    lst_only = qs.get("list", [None])[0]
    # /playlist?list=...
    if path.startswith("/playlist") and lst_only:
        return ParsedUrl(kind="playlist", list_id=lst_only,
                         normalised=f"https://www.youtube.com/playlist?list={lst_only}")

    # watch?v=...
    v = qs.get("v", [None])[0]
    if v and _VIDEO_ID_RE.match(v):
        if lst_only:
            return ParsedUrl(kind="playlist", video_id=v, list_id=lst_only,
                             normalised=f"https://www.youtube.com/watch?v={v}&list={lst_only}")
        return ParsedUrl(kind="video", video_id=v,
                         normalised=f"https://www.youtube.com/watch?v={v}")

    # /shorts/<id>, /live/<id>, /embed/<id>, /v/<id>
    m = re.match(r"^/(shorts|live|embed|v)/([A-Za-z0-9_-]{11})(?:/|$)", path)
    if m:
        vid = m.group(2)
        norm = f"https://www.youtube.com/watch?v={vid}"
        if lst_only:
            norm += f"&list={lst_only}"
            return ParsedUrl(kind="playlist", video_id=vid, list_id=lst_only, normalised=norm)
        return ParsedUrl(kind="video", video_id=vid, normalised=norm)

    # watch URL with playlist but odd video id -> still treat as playlist if list present
    if lst_only and _LIST_ID_RE.match(lst_only):
        return ParsedUrl(kind="playlist", list_id=lst_only,
                         normalised=f"https://www.youtube.com/playlist?list={lst_only}")

    return ParsedUrl(kind="invalid", reason="Could not find a video or playlist ID in this URL.")


def is_valid_youtube_url(raw: str) -> bool:
    return parse_youtube_url(raw).kind in ("video", "playlist")


def is_playlist_url(raw: str) -> bool:
    return parse_youtube_url(raw).kind == "playlist"


def validate_url(raw: str) -> tuple[bool, str]:
    """Return (ok, message_or_normalised). Used by UI + downloader."""
    p = parse_youtube_url(raw)
    if p.kind == "invalid":
        return False, p.reason or "Invalid YouTube URL."
    assert p.normalised
    return True, p.normalised
