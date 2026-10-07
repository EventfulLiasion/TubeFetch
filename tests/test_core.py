"""Utils + models tests — pure logic, no network."""
from pathlib import Path

from app import utils
from app.models import DownloadRequest, build_format_spec, merge_output_format, parse_raw_formats
from app.utils import format_bytes, format_duration, format_eta, sanitize_filename, unique_path


def test_sanitize_removes_illegal():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename("   ") == "untitled"
    assert sanitize_filename("CON") != "CON"


def test_sanitize_trims_dots_spaces():
    assert not sanitize_filename("  hello world. ").endswith(".")
    assert len(sanitize_filename("x" * 500)) <= 180


def test_unique_path_increments(tmp_path):
    (tmp_path / "video.mp4").write_text("x")
    p1 = unique_path(tmp_path, "video", ".mp4")
    assert p1.name == "video (1).mp4"
    p1.write_text("x")
    p2 = unique_path(tmp_path, "video", ".mp4")
    assert p2.name == "video (2).mp4"


def test_unique_path_sanitizes(tmp_path):
    p = unique_path(tmp_path, 'bad:name?', ".mp3")
    assert ":" not in p.name and "?" not in p.name


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(None) == "—"


def test_format_eta():
    assert format_eta(5) == "5s"
    assert format_eta(65) == "1m 05s"
    assert format_eta(None) == "—"


def test_format_duration():
    assert format_duration(65) == "1:05"
    assert format_duration(3665) == "1:01:05"


def test_parse_raw_formats_split():
    raw = [
        {"format_id": "18", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "mp4a",
         "filesize": 1000, "tbr": 500, "abr": None, "fps": 30},
        {"format_id": "137", "ext": "mp4", "height": 1080, "vcodec": "avc1", "acodec": "none",
         "filesize": 5000, "tbr": 4000, "abr": None, "fps": 30},
        {"format_id": "140", "ext": "m4a", "height": None, "vcodec": "none", "acodec": "mp4a",
         "filesize": 800, "tbr": 128, "abr": 128, "fps": None},
    ]
    c, v, a = parse_raw_formats(raw)
    assert len(c) == 1 and c[0].format_id == "18"
    assert len(v) == 1 and v[0].format_id == "137"
    assert len(a) == 1 and a[0].format_id == "140"


def test_build_format_spec_audio():
    req = DownloadRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                          mode="audio_only", quality="best", audio_format="mp3")
    fmt, pp = build_format_spec(req)
    assert "bestaudio" in fmt
    assert pp[0]["preferredcodec"] == "mp3"
    assert merge_output_format(req) == "mp3"


def test_build_format_spec_video_audio_best():
    req = DownloadRequest(url="x", mode="video_audio", quality="best", video_format="mp4")
    fmt, _ = build_format_spec(req)
    assert "bestvideo" in fmt and "bestaudio" in fmt
    assert merge_output_format(req) == "mp4"


def test_build_format_spec_specific_id():
    req = DownloadRequest(url="x", mode="video_audio", quality="137", video_format="mkv")
    fmt, _ = build_format_spec(req)
    assert fmt.startswith("137+")
    assert merge_output_format(req) == "mkv"


def test_friendly_error_mapping():
    assert "unavailable" in utils.friendly_error("ERROR: Video unavailable").lower()
    assert "connection" in utils.friendly_error("Failed: connection timed out").lower() or \
           "network" in utils.friendly_error("Failed: connection timed out").lower()
