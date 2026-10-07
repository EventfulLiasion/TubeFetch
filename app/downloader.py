"""Download orchestration: retries, cancellation, temp files, verification.

Qt-free so it can be unit-tested; the UI wraps it in QThreads.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import yt_dlp

from . import ffmpeg_utils
from .logger import get_logger, resolve_download_dir
from .models import DownloadProgress, DownloadRequest, DownloadResult
from .utils import friendly_error, sanitize_filename
from .validators import validate_url
from .ytdlp_service import build_ydl_options

log = get_logger(__name__)

TRANSIENT_MARKERS = (
    "timed out", "timeout", "temporary failure", "connection reset",
    "connection aborted", "http error 429", "http error 500",
    "http error 502", "http error 503", "http error 504", "ssl",
)


def is_transient(msg: str) -> bool:
    low = (msg or "").lower()
    return any(m in low for m in TRANSIENT_MARKERS)


class Cancelled(Exception):
    pass


class DownloadJob:
    """Single-video download with progress callbacks and cooperative cancel."""

    def __init__(self, req: DownloadRequest, ffmpeg: str | None = None,
                 max_retries: int = 3,
                 on_progress: Callable[[DownloadProgress], None] | None = None) -> None:
        self.req = req
        self.ffmpeg = ffmpeg or ffmpeg_utils.find_ffmpeg()
        self.max_retries = max(0, max_retries)
        self.on_progress = on_progress or (lambda _p: None)
        self._cancel = threading.Event()
        self._last_file: str | None = None
        self._seen_files: list[str] = []

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- yt-dlp progress hook -------------------------------------------------
    def _hook(self, d: dict) -> None:
        if self.cancelled:
            raise Cancelled("Cancelled by user.")
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            speed = d.get("speed")
            eta = d.get("eta")
            pct = (downloaded / total * 100) if total else None
            self._last_file = d.get("filename") or self._last_file
            if d.get("filename") and d["filename"] not in self._seen_files:
                self._seen_files.append(d["filename"])
            self.on_progress(DownloadProgress(
                downloaded_bytes=downloaded, total_bytes=total,
                speed_bps=speed, eta_secs=eta, percent=pct,
                stage="downloading", info=d.get("info_dict", {}).get("format", "") or "",
            ))
        elif status == "finished":
            self._last_file = d.get("filename") or self._last_file
            if d.get("filename") and d["filename"] not in self._seen_files:
                self._seen_files.append(d["filename"])
            self.on_progress(DownloadProgress(
                downloaded_bytes=d.get("total_bytes") or 0,
                total_bytes=d.get("total_bytes"),
                stage="merging" if self.req.mode == "video_audio" else "converting"
                if self.req.mode == "audio_only" else "downloading",
                info="Processing…",
            ))

    def _emit(self, stage: str, info: str) -> None:
        self.on_progress(DownloadProgress(stage=stage, info=info))

    # -- main entry ------------------------------------------------------------
    def run(self) -> DownloadResult:
        ok, norm_or_err = validate_url(self.req.url)
        if not ok:
            return DownloadResult(ok=False, error=norm_or_err, title=self.req.title)
        url = norm_or_err
        out_dir = resolve_download_dir(self.req.out_dir or ".")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return DownloadResult(ok=False, error=f"Cannot use download folder: {exc}")

        if self.req.mode in ("video_audio", "audio_only") and not self.ffmpeg:
            return DownloadResult(ok=False, error=(
                "FFmpeg is required for this download (merging/MP3 conversion) but was not found. "
                "Install FFmpeg or set its path in Settings."))

        stem = sanitize_filename(self.req.title or "youtube_download")
        ext = {"video_audio": self.req.video_format.lower(),
               "video_only": self.req.video_format.lower(),
               "audio_only": self.req.audio_format.lower()}.get(self.req.mode, "mp4")
        if ext == "opus":
            ext = "opus"
        # yt-dlp manages final name; use title-based template then verify.
        outtmpl = str(out_dir / f"{stem}.%(ext)s")

        attempt = 0
        while True:
            if self.cancelled:
                self._cleanup_partials(out_dir, stem)
                return DownloadResult(ok=False, error="Cancelled by user.", title=self.req.title)
            try:
                self._emit("downloading", f"Downloading… (attempt {attempt + 1})")
                opts = build_ydl_options(self.req, self.ffmpeg, self._hook, outtmpl)
                opts["concurrent_fragment_downloads"] = 4
                start_ts = time.time() - 5  # tolerance for clock skew
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                if self.cancelled:
                    self._cleanup_partials(out_dir, stem)
                    return DownloadResult(ok=False, error="Cancelled by user.", title=self.req.title)
                final = self._find_output(out_dir, stem, since=start_ts)
                if final is None:
                    # Last resort: ask yt-dlp what filename it would use (handles
                    # yt-dlp's own title sanitising, which can differ from ours).
                    final = self._resolve_via_prepare_filename(url, opts, out_dir, since=start_ts)
                if final is None or not final.exists():
                    raise RuntimeError(
                        "Download finished but the output file could not be located. "
                        f"Please check {out_dir} — the file may still be there under a slightly different name.")
                if not self._wait_for_stable_file(final, timeout_s=10):
                    raise RuntimeError(
                        f"Download finished but {final.name} is still empty — the download may have failed.")
                self._emit("downloading", "Done.")
                return DownloadResult(ok=True, filepath=str(final), title=self.req.title)
            except Cancelled:
                self._cleanup_partials(out_dir, stem)
                return DownloadResult(ok=False, error="Cancelled by user.", title=self.req.title)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                log.warning("download attempt %d failed: %s", attempt + 1, msg[:300])
                if self.cancelled:
                    self._cleanup_partials(out_dir, stem)
                    return DownloadResult(ok=False, error="Cancelled by user.", title=self.req.title)
                if attempt < self.max_retries and is_transient(msg):
                    attempt += 1
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if attempt < self.max_retries and "403" in msg:
                    attempt += 1
                    time.sleep(2)
                    continue
                self._cleanup_partials(out_dir, stem, keep_final=False)
                return DownloadResult(ok=False, error=friendly_error(msg), title=self.req.title)

    TEMP_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp")

    def _is_usable_file(self, p: Path) -> bool:
        return p.is_file() and p.suffix.lower() not in self.TEMP_SUFFIXES

    def _find_output(self, out_dir: Path, stem: str, since: float = 0) -> Path | None:
        """Locate the finished file. yt-dlp sanitises titles its own way, so the
        on-disk name may differ from our `stem` — try several strategies."""
        # 1) Exact stem match (any container), newest first.
        try:
            cands = [c for c in out_dir.glob(f"{stem}.*") if self._is_usable_file(c)]
            if cands:
                cands.sort(key=lambda p: p.stat().st_mtime)
                return cands[-1]
        except Exception:
            pass
        # 2) Filenames reported by yt-dlp progress hooks (handles merge/convert
        # renames: foo.mp4 + foo.m4a -> foo.mkv, or audio -> foo.mp3).
        for raw in reversed(self._seen_files):
            for cand in (Path(raw), Path(str(raw).removesuffix(".part"))):
                if not self._is_usable_file(cand):
                    continue
                # The hook may report an intermediate file; prefer same-stem
                # siblings (e.g. video.mp4 -> video.mp3 after conversion).
                try:
                    siblings = [s for s in cand.parent.glob(f"{cand.stem}.*")
                                if self._is_usable_file(s)]
                except Exception:
                    siblings = []
                if siblings:
                    siblings.sort(key=lambda p: p.stat().st_mtime)
                    return siblings[-1]
                return cand
        # 3) Newest non-temp file created during this download (fallback for
        # titles yt-dlp sanitised differently than we did).
        try:
            fresh = [p for p in out_dir.iterdir()
                     if self._is_usable_file(p) and p.stat().st_mtime >= since]
            if fresh:
                fresh.sort(key=lambda p: p.stat().st_mtime)
                return fresh[-1]
        except Exception:
            pass
        return None

    def _resolve_via_prepare_filename(self, url: str, opts: dict, out_dir: Path,
                                      since: float) -> Path | None:
        """Ask yt-dlp for the expected filename without downloading."""
        try:
            probe = dict(opts)
            probe.pop("progress_hooks", None)
            probe["quiet"] = True
            probe["no_warnings"] = True
            probe["skip_download"] = True
            with yt_dlp.YoutubeDL(probe) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None
                name = ydl.prepare_filename(info)
            p = Path(name)
            if self._is_usable_file(p):
                return p
            # Extension may change after merge/extract (mp4->mkv, any->mp3):
            siblings = [s for s in p.parent.glob(f"{p.stem}.*") if self._is_usable_file(s)]
            if siblings:
                siblings.sort(key=lambda x: x.stat().st_mtime)
                return siblings[-1]
        except Exception as exc:
            log.debug("prepare_filename fallback failed: %s", exc)
        return None

    @staticmethod
    def _wait_for_stable_file(p: Path, timeout_s: float = 10) -> bool:
        """Wait until the file exists and is non-empty (merge/AV-scan can lag)."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if p.exists() and p.stat().st_size > 0:
                    return True
            except OSError:
                pass
            time.sleep(0.5)
        try:
            return p.exists() and p.stat().st_size > 0
        except OSError:
            return False

    def _cleanup_partials(self, out_dir: Path, stem: str, keep_final: bool = False) -> None:
        try:
            targets: set[Path] = set(out_dir.glob(f"{stem}.*"))
            for raw in list(self._seen_files) + ([self._last_file] if self._last_file else []):
                if not raw:
                    continue
                targets.add(Path(raw))
                targets.add(Path(str(raw).removesuffix(".part")))
            for p in targets:
                try:
                    if p.suffix.lower() in self.TEMP_SUFFIXES and p.exists():
                        p.unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            pass
