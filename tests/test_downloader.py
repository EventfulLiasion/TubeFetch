"""Downloader logic tests with mocked yt-dlp (no network)."""
from pathlib import Path

from app.downloader import DownloadJob, is_transient
from app.models import DownloadRequest


def test_is_transient():
    assert is_transient("Read timed out")
    assert is_transient("HTTP Error 503: Service Unavailable")
    assert not is_transient("Video unavailable")
    # Non-transient completion-check failures must NOT be retried as network
    # issues (regression: "error" substring used to force retries).
    assert not is_transient("Download finished but the output file could not be located.")


def test_find_output_falls_back_to_fresh_file(tmp_path):
    """yt-dlp sanitises titles its own way; a name mismatch must still resolve."""
    import time

    real = tmp_path / "Rick Astley - Never Gonna Give You Up (Official Video).mp4"
    real.write_bytes(b"x" * 64)
    req = DownloadRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                          mode="video_only", quality="best",
                          out_dir=str(tmp_path), title="different_stem")
    job = DownloadJob(req, ffmpeg="dummy", max_retries=0)
    assert job._find_output(tmp_path, "different_stem", since=time.time() - 60) == real


def test_wait_for_stable_file(tmp_path):
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    assert DownloadJob._wait_for_stable_file(empty, timeout_s=1) is False
    full = tmp_path / "f.mp4"
    full.write_bytes(b"data")
    assert DownloadJob._wait_for_stable_file(full, timeout_s=2) is True


def test_cancelled_before_start(tmp_path):
    req = DownloadRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                          mode="video_only", quality="worst",
                          out_dir=str(tmp_path), title="t")
    job = DownloadJob(req, ffmpeg="dummy", max_retries=0)
    job.cancel()
    res = job.run()
    assert not res.ok and res.error == "Cancelled by user."


def test_invalid_url_rejected(tmp_path):
    req = DownloadRequest(url="https://vimeo.com/123", mode="video_only",
                          quality="best", out_dir=str(tmp_path), title="t")
    job = DownloadJob(req, ffmpeg="dummy", max_retries=0)
    res = job.run()
    assert not res.ok


def test_missing_ffmpeg_for_mux(tmp_path):
    req = DownloadRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                          mode="video_audio", quality="best",
                          out_dir=str(tmp_path), title="t")
    job = DownloadJob(req, ffmpeg=None, max_retries=0)
    # monkeypatch find_ffmpeg to None
    import app.downloader as dl
    orig = dl.ffmpeg_utils.find_ffmpeg
    dl.ffmpeg_utils.find_ffmpeg = lambda *a, **k: None
    try:
        job2 = DownloadJob(req, ffmpeg=None, max_retries=0)
        job2.ffmpeg = None
        res = job2.run()
        assert not res.ok and "FFmpeg" in res.error
    finally:
        dl.ffmpeg_utils.find_ffmpeg = orig


def test_success_path_mocked(tmp_path, monkeypatch):
    import yt_dlp

    outdir = tmp_path / "out"
    outdir.mkdir()

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            # capture progress hook
            self.hook = (opts.get("progress_hooks") or [lambda d: None])[0]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            # simulate progress then write file per outtmpl
            tmpl = self.opts["outtmpl"].replace("%(ext)s", "mp4")
            Path(tmpl).write_bytes(b"fake-video-bytes")
            self.hook({"status": "downloading", "downloaded_bytes": 10,
                       "total_bytes": 20, "speed": 1000, "eta": 1, "filename": tmpl})
            self.hook({"status": "finished", "filename": tmpl, "total_bytes": 20})

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    req = DownloadRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                          mode="video_only", quality="best",
                          out_dir=str(outdir), title="My Video")
    seen = []
    job = DownloadJob(req, ffmpeg="dummy", max_retries=0, on_progress=seen.append)
    res = job.run()
    assert res.ok and res.filepath and Path(res.filepath).exists()
    assert seen, "progress callbacks should fire"
