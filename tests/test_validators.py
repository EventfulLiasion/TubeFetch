"""Validator tests — no network."""
from app.validators import is_playlist_url, is_valid_youtube_url, parse_youtube_url


def test_watch_url():
    p = parse_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert p.kind == "video" and p.video_id == "dQw4w9WgXcQ"


def test_short_link():
    p = parse_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert p.kind == "video"


def test_shorts():
    p = parse_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert p.kind == "video"


def test_no_scheme():
    assert is_valid_youtube_url("www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_playlist():
    p = parse_youtube_url("https://www.youtube.com/playlist?list=PL1234567890abcd")
    assert p.kind == "playlist"


def test_watch_with_list_is_playlist():
    assert is_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234")


def test_invalid_rejected():
    assert not is_valid_youtube_url("https://vimeo.com/12345")
    assert not is_valid_youtube_url("")
    assert not is_valid_youtube_url("not a url")
    assert not is_valid_youtube_url("https://www.youtube.com/")


def test_music_domain():
    p = parse_youtube_url("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
    assert p.kind == "video"
