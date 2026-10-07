"""Shared data models + format-selection helpers (pure logic, unit-tested)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    FETCHING = "fetching"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    CONVERTING = "converting"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass
class FormatOption:
    format_id: str
    label: str          # human label e.g. "1080p MP4 (avc1)"
    height: int | None  # None for audio
    ext: str
    vcodec: str
    acodec: str
    filesize: int | None
    tbr: float | None
    abr: float | None
    fps: float | None
    kind: str           # "combined" | "video-only" | "audio-only"

    def short(self) -> str:
        size = f" ~{self.filesize / 1048576:.0f} MB" if self.filesize else ""
        return f"{self.label}{size}"


@dataclass
class VideoInfo:
    video_id: str
    title: str
    uploader: str | None
    duration: int | None  # seconds
    view_count: int | None
    thumbnail: str | None
    webpage_url: str
    combined: list[FormatOption] = field(default_factory=list)
    video_only: list[FormatOption] = field(default_factory=list)
    audio_only: list[FormatOption] = field(default_factory=list)

    @property
    def best_combined(self) -> FormatOption | None:
        return self.combined[-1] if self.combined else None


@dataclass
class PlaylistItem:
    index: int
    video_id: str
    title: str
    url: str
    duration: int | None = None


@dataclass
class PlaylistInfo:
    title: str | None
    uploader: str | None
    count: int
    items: list[PlaylistItem]
    webpage_url: str


@dataclass
class DownloadRequest:
    url: str
    mode: str            # "video_audio" | "video_only" | "audio_only"
    quality: str         # format_id chosen, or "best"/"worst"
    video_format: str = "mp4"   # mp4|mkv|webm  (container for video modes)
    audio_format: str = "mp3"   # mp3|m4a|opus|wav
    out_dir: str = ""
    title: str = ""


@dataclass
class DownloadProgress:
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed_bps: float | None = None
    eta_secs: int | None = None
    percent: float | None = None
    stage: str = "downloading"  # downloading|merging|converting|fetching
    info: str = ""


@dataclass
class DownloadResult:
    ok: bool
    filepath: str | None = None
    error: str | None = None
    title: str = ""


# ---------------------------------------------------------------- helpers

def _codec_short(c: str | None) -> str:
    if not c or c == "none":
        return "—"
    c = c.lower()
    if "avc" in c or "h264" in c:
        return "H.264"
    if "hevc" in c or "h265" in c or c.startswith("hev"):
        return "H.265"
    if "av01" in c:
        return "AV1"
    if "vp09" in c or "vp9" in c:
        return "VP9"
    if "mp4a" in c:
        return "AAC"
    if "opus" in c:
        return "Opus"
    if "vorbis" in c:
        return "Vorbis"
    return c.split(".")[0].upper()


def format_option_from_raw(f: dict, kind: str) -> FormatOption:
    h = f.get("height")
    ext = (f.get("ext") or "").lower()
    vcodec = f.get("vcodec") or "none"
    acodec = f.get("acodec") or "none"
    if kind == "combined":
        res = f"{h}p" if h else (f.get("format_note") or "SD")
        label = f"{res} · {ext.upper()} · {_codec_short(vcodec)}+{_codec_short(acodec)}"
    elif kind == "video-only":
        res = f"{h}p" if h else (f.get("format_note") or "?")
        fps = f.get("fps")
        fps_s = f" {int(fps)}fps" if fps and fps > 30 else ""
        label = f"{res}{fps_s} · {ext.upper()} · {_codec_short(vcodec)} (no audio)"
    else:
        abr = f.get("abr") or f.get("tbr")
        abr_s = f" ~{int(abr)}k" if abr else ""
        label = f"Audio{abr_s} · {ext.upper()} · {_codec_short(acodec)}"
    return FormatOption(
        format_id=str(f.get("format_id")),
        label=label,
        height=h if isinstance(h, int) else None,
        ext=ext,
        vcodec=vcodec,
        acodec=acodec,
        filesize=f.get("filesize") or f.get("filesize_approx"),
        tbr=f.get("tbr"),
        abr=f.get("abr"),
        fps=f.get("fps"),
        kind=kind,
    )


def parse_raw_formats(raw_formats: list[dict]) -> tuple[list[FormatOption], list[FormatOption], list[FormatOption]]:
    """Split raw yt-dlp formats into combined / video-only / audio-only, sorted ascending."""
    combined, video_only, audio_only = [], [], []
    for f in raw_formats or []:
        if not f.get("format_id") or f.get("format_id") in ("sb0", "sb1", "sb2", "sb3"):
            continue
        if f.get("protocol") in ("m3u8_native", "m3u8") and not f.get("url"):
            continue
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        has_v = vcodec != "none"
        has_a = acodec != "none"
        if has_v and has_a:
            combined.append(format_option_from_raw(f, "combined"))
        elif has_v:
            video_only.append(format_option_from_raw(f, "video-only"))
        elif has_a:
            audio_only.append(format_option_from_raw(f, "audio-only"))
    key = lambda o: ((o.height or 0), (o.tbr or 0), (o.filesize or 0))
    combined.sort(key=key)
    video_only.sort(key=key)
    audio_only.sort(key=lambda o: ((o.abr or o.tbr or 0), (o.filesize or 0)))
    # de-dupe labels keeping largest
    def dedupe(opts: list[FormatOption]) -> list[FormatOption]:
        seen: dict[str, FormatOption] = {}
        for o in opts:
            seen[o.label] = o
        ordered = [seen[k] for k in seen]
        ordered.sort(key=key if opts is not combined else key)
        return ordered
    return dedupe(combined), dedupe(video_only), dedupe(audio_only)


def build_format_spec(req: DownloadRequest) -> tuple[str, list[dict]]:
    """Map a DownloadRequest to a yt-dlp `format` string + postprocessors.

    Returns (format_string, postprocessors).
    """
    q = (req.quality or "best").strip()
    if req.mode == "audio_only":
        fmt = q if q not in ("best", "worst") else ("bestaudio/best" if q == "best" else "worstaudio/worst")
        af = req.audio_format.lower()
        if af == "mp3":
            pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        elif af == "m4a":
            pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "192"}]
        elif af == "opus":
            pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "opus", "preferredquality": "192"}]
        elif af == "wav":
            pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]
        else:
            pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        return fmt, pp
    if req.mode == "video_only":
        fmt = q if q not in ("best", "worst") else ("bestvideo/best" if q == "best" else "worstvideo/worst")
        return fmt, []
    # video + audio: mux to requested container
    vf = req.video_format.lower() if req.video_format.lower() in ("mp4", "mkv", "webm") else "mp4"
    if q in ("best", "worst"):
        fmt = "bestvideo+bestaudio/best" if q == "best" else "worstvideo+worstaudio/worst"
    else:
        fmt = f"{q}+bestaudio/best"
    pp = [{"key": "FFmpegVideoConvertor", "preferedformat": vf}] if False else []
    # Use merge_output_format so yt-dlp muxes with ffmpeg.
    return fmt, pp


def merge_output_format(req: DownloadRequest) -> str | None:
    if req.mode == "audio_only":
        return req.audio_format.lower()
    if req.mode in ("video_audio", "video_only"):
        vf = req.video_format.lower()
        return vf if vf in ("mp4", "mkv", "webm") else "mp4"
    return None
