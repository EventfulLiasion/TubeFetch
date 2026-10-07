# TubeFetch — YouTube Media Downloader

A polished desktop app for downloading YouTube videos and audio **you are authorized to
download** (your own uploads, Creative-Commons / public-domain works, or content whose
owner permits downloading). Built with **PySide6 (Qt) + yt-dlp + FFmpeg**.

> ⚖️ **Legal note:** respect YouTube's Terms of Service and copyright law. Do not download
> content you don't have rights to. The authors are not responsible for misuse.

![stack](https://img.shields.io/badge/Qt-PySide6-green) ![yt-dlp](https://img.shields.io/badge/media-yt--dlp-blue) ![ffmpeg](https://img.shields.io/badge/mux-FFmpeg-orange)

## Features

- Paste any `youtube.com/watch`, `youtu.be`, Shorts/Live/Embed, or **playlist** link
- Validates URLs locally, then fetches title / uploader / duration / views / thumbnail
- Quality pickers: **combined**, **video-only**, **audio-only** streams with codec + size labels
- Modes: **Video + audio (auto-muxed to MP4/MKV/WebM)**, **Video only**, **Audio only (MP3/M4A/Opus/WAV)**
- Accurate progress: bar, %, downloaded/total, speed, ETA, merging/converting stage
- **Cancel** anytime — partial (`.part`) files are cleaned up, never presented as success
- Transient network failures retried with backoff (HTTP 429/5xx, timeouts, resets)
- Filename sanitising + duplicate handling (`name (1).mp4`, …)
- Playlists: per-item progress, failed items **skipped** (not fatal), summary at the end
- Queue multiple links, persistent **history**, remembered preferences (folder, quality, formats)
- Settings page: download folder, defaults, retries, max playlist items, FFmpeg path
- Structured logging to file + on-screen console

## Project layout

```
tubefetch/
  run.py               # dev launcher  →  python run.py
  requirements.txt
  pyproject.toml
  installer.spec       # PyInstaller build
  app/
    __init__.py        # version
    __main__.py        # python -m app
    main.py            # PySide6 UI (threads, never blocks)
    ytdlp_service.py   # metadata fetch + format parsing (yt-dlp, maintained lib)
    downloader.py      # retries, cancel, temp-file safety, verification (Qt-free, tested)
    models.py          # VideoInfo / PlaylistInfo / format-spec builders (tested)
    validators.py      # URL validation, no network (tested)
    utils.py           # sanitize, unique paths, formatting, friendly errors (tested)
    config.py          # JSON preferences + history (tested)
    ffmpeg_utils.py    # ffmpeg discovery (PATH → imageio-ffmpeg bundle) + mux helper
    logger.py          # file + console logging
  tests/               # pytest, no network (yt-dlp mocked where needed)
```

## Installation (Windows / macOS / Linux)

Requires **Python 3.10+**.

```bash
cd tubefetch
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

**FFmpeg** is required for video+audio merging and MP3/M4A conversion. TubeFetch looks for it in
this order: `Settings → FFmpeg binary` path, system `PATH`, then the `imageio-ffmpeg` bundled
binary (installed automatically via requirements). To install system-wide:

- Windows: `winget install Gyan.FFmpeg` (or `choco install ffmpeg`)
- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`

## Run (development)

```bash
python run.py
# or
python -m app
```

## Usage

1. Paste a YouTube link → **Fetch**.
2. Review the preview (thumbnail, title, uploader, duration).
3. Choose mode (**Video+audio / Video only / Audio only**), quality, and container.
4. Choose the save folder → **Download**.
5. Watch live progress (speed/ETA). **Cancel** anytime if needed.
6. Playlists: Fetch a playlist link → **Download** processes each item, skipping failures.
7. Queue tab: build a multi-link queue; History tab: past downloads; Settings tab: preferences.

## Build a standalone executable

```bash
pip install -r requirements.txt
python -m PyInstaller installer.spec --noconfirm
# → dist/TubeFetch/TubeFetch.exe  (Windows)
#    dist/TubeFetch/TubeFetch      (macOS/Linux)
```

PyInstaller bundles Python + Qt + yt-dlp. FFmpeg is resolved at runtime (system install preferred;
otherwise the `imageio-ffmpeg` binary is used — no credentials or tokens are ever bundled).

## Tests

```bash
python -m pytest tests -v
```

28 tests covering URL validation, filename safety, format-spec building, config/history
persistence, retry/cancel logic, and a mocked end-to-end download (no network required).

Real-world checks performed before release:

| Check | Result |
|---|---|
| App compiles + launches (incl. offscreen smoke test) | ✅ |
| Valid video fetch (Never Gonna Give You Up) | ✅ title + 30 video-only + 5 audio streams |
| Invalid URLs (vimeo / empty / garbage) rejected with message | ✅ |
| Cancellation cleans `.part` files, never reports success | ✅ |
| Video+audio mux via FFmpeg | ✅ verified byte-identical pipeline |
| Audio-only MP3 conversion | ✅ |
| Transient-failure retry + friendly errors | ✅ |

## Configuration

Stored per-user (no secrets, no cookies, no tokens):

- Windows: `%LOCALAPPDATA%\TubeFetch\settings.json`, `history.json`, `tubefetch.log`
- macOS: `~/Library/Application Support/TubeFetch/`
- Linux: `~/.config/tubefetch/`

### Portable / relocatable mode

Want the app, its settings/history, **and** your downloads in one folder you can move
(e.g. to another drive or PC)?

1. Create an empty file named `PORTABLE.txt` next to `run.py` (source) or next to
   `TubeFetch.exe` (packaged build).
2. Start the app once — it creates `portable-data/` there containing
   `settings.json`, `history.json`, `tubefetch.log`, and `Downloads/`.
3. Move/copy the whole folder wherever you like; everything keeps working because
   paths resolve relative to the app folder.

Notes:

- Without `PORTABLE.txt`, moving the project folder still works — the download
  folder is an absolute path (`C:\Users\...\Downloads\TubeFetch` by default), so it
  just stays where it was instead of moving with the app.
- A *relative* download folder (e.g. `my-downloads` typed into Settings) always
  resolves inside the project folder, portable mode or not.
- Delete `PORTABLE.txt` (and `portable-data/` if you wish) to go back to per-user
  `%LOCALAPPDATA%` storage.

## Security & reliability notes

- All user input validated before use; URLs restricted to YouTube hosts.
- Filenames sanitised (`<>:"/\|?*` → `_`), Windows-reserved names guarded, over-long names cut.
- Downloads go to `.part`-style temp handling with atomic completion checks (missing/empty file = failure).
- No hardcoded URLs, tokens, cookies, or credentials anywhere.
- yt-dlp (actively maintained) handles YouTube protocols; FFmpeg handles mux/convert.
- UI never blocks: fetching and downloading run in `QThread`s with progress signals.

## Updating yt-dlp

YouTube changes break extractors occasionally; update with:

```bash
pip install -U yt-dlp
```

## License

MIT — see LICENSE (add one if distributing). You are responsible for complying with
YouTube's Terms and applicable copyright law.
