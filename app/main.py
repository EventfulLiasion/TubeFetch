"""TubeFetch — polished YouTube downloader desktop app (PySide6 + yt-dlp + FFmpeg)."""
from __future__ import annotations

import datetime
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget, QHeaderView, QButtonGroup, QCheckBox,
)

from . import __version__
from .config import AppConfig, ConfigManager, HistoryStore
from .downloader import DownloadJob
from .logger import get_logger, setup_logging
from .models import DownloadRequest, DownloadProgress, PlaylistInfo, VideoInfo
from .utils import format_bytes, format_duration, format_eta, format_speed
from .validators import is_valid_youtube_url, parse_youtube_url
from . import ytdlp_service

log = get_logger("tubefetch.ui")

ACCENT = "#ff3355"

APP_QSS = f"""
* {{ font-family: 'Segoe UI', 'Inter', sans-serif; }}
QMainWindow, QWidget {{ background: #0f1115; color: #e8eaf0; }}
QTabWidget::pane {{ border: 1px solid #23262f; border-radius: 10px; background: #14171d; }}
QTabBar::tab {{ background: #1a1e26; color: #aab0c0; padding: 9px 22px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 4px; }}
QTabBar::tab:selected {{ background: #232936; color: white; }}
QGroupBox {{ border: 1px solid #23262f; border-radius: 10px; margin-top: 14px; padding-top: 10px; background: #14171d; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #9aa3b5; }}
QLineEdit, QComboBox, QSpinBox, QTextEdit, QListWidget, QTableWidget {{
  background: #1a1e26; border: 1px solid #2a3040; border-radius: 8px; padding: 8px; color: #eef1f7; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QPushButton {{ background: #222836; border: 1px solid #2f3648; border-radius: 8px; padding: 9px 16px; color: #eef1f7; font-weight: 600; }}
QPushButton:hover {{ background: #2b3245; }}
QPushButton:disabled {{ color: #6b7280; background: #1a1e26; }}
QPushButton#primary {{ background: {ACCENT}; border: none; color: white; font-size: 14px; }}
QPushButton#primary:hover {{ background: #e11d48; }}
QPushButton#primary:disabled {{ background: #5b2230; color: #a08a90; }}
QProgressBar {{ background: #1a1e26; border: 1px solid #2a3040; border-radius: 8px; height: 18px; text-align: center; color: white; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff3355, stop:1 #ff7847); border-radius: 7px; }}
QLabel#muted {{ color: #9aa3b5; }}
QLabel#title {{ font-size: 17px; font-weight: 700; }}
QLabel#header {{ font-size: 22px; font-weight: 800; }}
QRadioButton {{ spacing: 8px; padding: 6px; }}
QTextEdit#console {{ font-family: Consolas, monospace; font-size: 12px; }}
"""


# ---------------------------------------------------------------- workers

class FetchThread(QThread):
    done = Signal(object)   # VideoInfo | PlaylistInfo
    failed = Signal(str)

    def __init__(self, url: str, max_items: int) -> None:
        super().__init__()
        self.url = url
        self.max_items = max_items

    def run(self) -> None:
        try:
            info = ytdlp_service.fetch_media(self.url, self.max_items)
            self.done.emit(info)
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch failed: %s", exc)
            self.failed.emit(str(exc))


class DownloadThread(QThread):
    progress = Signal(object)  # DownloadProgress
    finished_ok = Signal(object)  # DownloadResult
    finished_fail = Signal(object)

    def __init__(self, req: DownloadRequest, ffmpeg: str | None, retries: int) -> None:
        super().__init__()
        self.req = req
        self.ffmpeg = ffmpeg
        self.retries = retries
        self.job: DownloadJob | None = None

    def run(self) -> None:
        try:
            self.job = DownloadJob(self.req, ffmpeg=self.ffmpeg, max_retries=self.retries,
                                   on_progress=lambda p: self.progress.emit(p))
            res = self.job.run()
            if res.ok:
                self.finished_ok.emit(res)
            else:
                self.finished_fail.emit(res)
        except Exception as exc:  # noqa: BLE001
            log.exception("download thread crashed")
            from .models import DownloadResult
            self.finished_fail.emit(DownloadResult(ok=False, error=str(exc), title=self.req.title))

    def cancel(self) -> None:
        if self.job:
            self.job.cancel()


# ---------------------------------------------------------------- main window

@dataclass
class QueueEntry:
    url: str
    title: str
    mode: str
    quality: str
    quality_label: str = ""
    video_format: str = "mp4"
    audio_format: str = "mp3"
    out_dir: str = ""


class MainWindow(QMainWindow):
    def __init__(self, cfg_mgr: ConfigManager, history: HistoryStore) -> None:
        super().__init__()
        self.cfg_mgr = cfg_mgr
        self.cfg: AppConfig = cfg_mgr.config
        self.history = history
        self.setWindowTitle(f"TubeFetch {__version__} — YouTube Downloader")
        self.resize(1120, 840)
        self.setMinimumSize(900, 650)
        self.video: VideoInfo | None = None
        self.playlist: PlaylistInfo | None = None
        self.fetch_thread: FetchThread | None = None
        self._fetch_gen = 0
        self._fetch_orphans: list = []  # cancelled threads, kept alive till they exit
        self.dl_thread: DownloadThread | None = None
        self.playlist_cancel = False
        self.queue: list[QueueEntry] = []
        self._ffmpeg: str | None = None
        # Sticky quality preference, per mode, so Fetch #2 doesn't silently
        # reset the picker to highest (which used to poison later queue adds).
        self._sticky: dict[str, dict] = {}
        self._quality_meta: list[dict] = []
        self._populating = False
        self._build_ui()
        self._refresh_settings_ui()
        self._refresh_history_table()
        self._check_ffmpeg_async()

    # -- layout ----------------------------------------------------------
    def _build_ui(self) -> None:
        self.setStyleSheet(APP_QSS)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("◉ TubeFetch")
        title.setObjectName("header")
        sub = QLabel("Download videos & audio you’re authorized to keep  •  MP4 · MP3 · playlists")
        sub.setObjectName("muted")
        header.addWidget(title)
        header.addSpacing(12)
        header.addWidget(sub, stretch=1)
        self.ffmpeg_badge = QLabel("checking FFmpeg…")
        self.ffmpeg_badge.setObjectName("muted")
        header.addWidget(self.ffmpeg_badge)
        root.addLayout(header)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, stretch=1)
        self.tabs.addTab(self._download_tab(), "⬇  Download")
        self.tabs.addTab(self._queue_tab(), "☰  Queue & History")
        self.tabs.addTab(self._settings_tab(), "⚙  Settings")

        # menu
        m = self.menuBar()
        help_menu = m.addMenu("Help")
        a = QAction("Open download folder", self)
        a.triggered.connect(self._open_download_dir)
        help_menu.addAction(a)
        about = QAction("About TubeFetch", self)
        about.triggered.connect(lambda: QMessageBox.information(
            self, "About TubeFetch",
            "TubeFetch %s\n\nPaste a YouTube link, pick quality, download.\n"
            "Only download content you own or are authorized to download.\nRespect YouTube's Terms and copyright law." % __version__))
        help_menu.addAction(about)

    def _download_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        from PySide6.QtWidgets import QScrollArea, QSizePolicy
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(10)
        lay.setContentsMargins(2, 2, 2, 2)

        # URL card
        url_box = QGroupBox("1 · Paste a YouTube link")
        gl = QHBoxLayout(url_box)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=…  or  https://youtu.be/…  or playlist link")
        self.url_edit.returnPressed.connect(self.on_fetch)
        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("primary")
        self.fetch_btn.clicked.connect(self.on_fetch)
        paste_btn = QPushButton("Paste")
        paste_btn.clicked.connect(lambda: self.url_edit.setText(QApplication.clipboard().text().strip()))
        gl.addWidget(self.url_edit, stretch=1)
        gl.addWidget(paste_btn)
        gl.addWidget(self.fetch_btn)
        lay.addWidget(url_box)

        mid = QHBoxLayout()
        # preview card
        prev = QGroupBox("Preview")
        pl = QVBoxLayout(prev)
        self.thumb_label = QLabel("No video loaded yet.\nPaste a link and press Fetch.")
        self.thumb_label.setObjectName("muted")
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setMinimumSize(280, 160)
        self.thumb_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.thumb_label.setStyleSheet("background:#0b0d12;border-radius:8px;")
        pl.addWidget(self.thumb_label)
        self.info_title = QLabel("")
        self.info_title.setObjectName("title")
        self.info_title.setWordWrap(True)
        pl.addWidget(self.info_title)
        self.info_meta = QLabel("")
        self.info_meta.setObjectName("muted")
        self.info_meta.setWordWrap(True)
        pl.addWidget(self.info_meta)
        self.playlist_list = QListWidget()
        self.playlist_list.setMaximumHeight(130)
        self.playlist_list.hide()
        pl.addWidget(self.playlist_list)

        # options card
        opt = QGroupBox("2 · Choose format & quality")
        ol = QVBoxLayout(opt)
        self.mode_group = QButtonGroup(self)
        self.rb_both = QRadioButton("🎬  Video + audio (merged)")
        self.rb_vonly = QRadioButton("🎞  Video only (no sound)")
        self.rb_aonly = QRadioButton("🎵  Audio only")
        for i, rb in enumerate((self.rb_both, self.rb_vonly, self.rb_aonly)):
            self.mode_group.addButton(rb, i)
            ol.addWidget(rb)
        self.rb_both.setChecked(True)
        self.mode_group.buttonClicked.connect(self._mode_changed)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.quality_combo = QComboBox()
        self.quality_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.quality_combo.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        form.addRow("Quality:", self.quality_combo)
        self.vfmt_combo = QComboBox()
        self.vfmt_combo.addItems(["mp4", "mkv", "webm"])
        form.addRow("Video container:", self.vfmt_combo)
        self.afmt_combo = QComboBox()
        self.afmt_combo.addItems(["mp3", "m4a", "opus", "wav"])
        form.addRow("Audio format:", self.afmt_combo)
        ol.addLayout(form)

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(self.cfg.download_dir)
        self.dir_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        browse = QPushButton("Browse…")
        browse.setFixedWidth(100)
        browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.dir_edit, stretch=1)
        dir_row.addWidget(browse)
        save_label = QLabel("Save to:")
        ol.addWidget(save_label)
        ol.addLayout(dir_row)
        # keep the two cards side-by-side on wide windows but never clip:
        # allow the row to shrink and let the scroll area take over on narrow ones
        prev.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        opt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        prev.setMinimumWidth(280)
        opt.setMinimumWidth(300)
        mid.addWidget(prev, stretch=3)
        mid.addWidget(opt, stretch=2)
        lay.addLayout(mid)

        # download card
        dl = QGroupBox("3 · Download")
        dl_lay = QVBoxLayout(dl)
        btn_row = QHBoxLayout()
        self.dl_btn = QPushButton("⬇  Download")
        self.dl_btn.setObjectName("primary")
        self.dl_btn.setEnabled(False)
        self.dl_btn.clicked.connect(self.on_download)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.on_cancel)
        self.queue_btn = QPushButton("＋ Add to queue")
        self.queue_btn.setEnabled(False)
        self.queue_btn.clicked.connect(self.on_add_queue)
        btn_row.addWidget(self.dl_btn, stretch=2)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.queue_btn)
        dl_lay.addLayout(btn_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        dl_lay.addWidget(self.progress)
        self.status_label = QLabel("Idle.")
        self.status_label.setObjectName("muted")
        dl_lay.addWidget(self.status_label)
        self.console = QTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(120)
        dl_lay.addWidget(self.console)
        lay.addWidget(dl)
        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return w

    def _queue_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        qb = QGroupBox("Download queue")
        ql = QVBoxLayout(qb)
        self.queue_list = QListWidget()
        ql.addWidget(self.queue_list)
        row = QHBoxLayout()
        self.q_start = QPushButton("Start queue")
        self.q_start.clicked.connect(self.on_start_queue)
        self.q_skip = QPushButton("Remove selected")
        self.q_skip.clicked.connect(self.on_remove_selected)
        self.q_clear = QPushButton("Clear")
        self.q_clear.clicked.connect(lambda: (self.queue.clear(), self._refresh_queue()))
        row.addWidget(self.q_start)
        row.addWidget(self.q_skip)
        row.addWidget(self.q_clear)
        ql.addLayout(row)
        self.q_status = QLabel("")
        self.q_status.setObjectName("muted")
        ql.addWidget(self.q_status)
        lay.addWidget(qb)

        hb = QGroupBox("History (this device)")
        hl = QVBoxLayout(hb)
        self.hist_table = QTableWidget(0, 4)
        self.hist_table.setHorizontalHeaderLabels(["When", "Title", "File", "Status"])
        self.hist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.hist_table.setEditTriggers(QTableWidget.NoEditTriggers)
        hl.addWidget(self.hist_table)
        hrow = QHBoxLayout()
        open_btn = QPushButton("Open download folder")
        open_btn.clicked.connect(self._open_download_dir)
        clear_h = QPushButton("Clear history")
        clear_h.clicked.connect(lambda: (self.history.clear(), self._refresh_history_table()))
        hrow.addWidget(open_btn)
        hrow.addWidget(clear_h)
        hl.addLayout(hrow)
        lay.addWidget(hb)
        return w

    def _settings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        sb = QGroupBox("Preferences (saved automatically)")
        form = QFormLayout(sb)
        self.s_dir = QLineEdit()
        s_browse = QPushButton("Browse…")
        s_browse.clicked.connect(lambda: self._browse_dir(target=self.s_dir))
        dir_h = QHBoxLayout()
        dir_h.addWidget(self.s_dir, stretch=1)
        dir_h.addWidget(s_browse)
        dir_w = QWidget()
        dir_w.setLayout(dir_h)
        form.addRow("Download folder:", dir_w)
        self.s_mode = QComboBox()
        self.s_mode.addItems(["video_audio", "video_only", "audio_only"])
        form.addRow("Default mode:", self.s_mode)
        self.s_vfmt = QComboBox()
        self.s_vfmt.addItems(["mp4", "mkv", "webm"])
        form.addRow("Video container:", self.s_vfmt)
        self.s_afmt = QComboBox()
        self.s_afmt.addItems(["mp3", "m4a", "opus", "wav"])
        form.addRow("Audio format:", self.s_afmt)
        self.s_retries = QSpinBox()
        self.s_retries.setRange(0, 10)
        form.addRow("Retries on failure:", self.s_retries)
        self.s_maxpl = QSpinBox()
        self.s_maxpl.setRange(1, 200)
        form.addRow("Max playlist items:", self.s_maxpl)
        self.s_ffmpeg = QLineEdit()
        self.s_ffmpeg.setPlaceholderText("auto-detect (leave empty)")
        ff_h = QHBoxLayout()
        ff_h.addWidget(self.s_ffmpeg, stretch=1)
        ff_browse = QPushButton("Browse…")
        ff_browse.clicked.connect(lambda: self._browse_file(target=self.s_ffmpeg))
        ff_h.addWidget(ff_browse)
        ff_w = QWidget()
        ff_w.setLayout(ff_h)
        form.addRow("FFmpeg binary:", ff_w)
        self.s_ffver = QLabel("")
        self.s_ffver.setObjectName("muted")
        form.addRow("", self.s_ffver)
        save = QPushButton("Save settings")
        save.setObjectName("primary")
        save.clicked.connect(self._save_settings_from_ui)
        form.addRow("", save)
        lay.addWidget(sb)
        note = QLabel("Only download videos you own, that are Creative-Commons/Public-Domain, "
                      "or where the owner permits downloading. Respect YouTube's Terms of Service and copyright law.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    # -- helpers ---------------------------------------------------------
    def _log(self, msg: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.console.append(f"[{ts}] {msg}")
        log.info(msg)

    def _browse_dir(self, target: QLineEdit | None = None) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose download folder",
                                             (target.text() if target else self.dir_edit.text()) or str(Path.home()))
        if d:
            (target or self.dir_edit).setText(d)
            if target is None:
                self.s_dir.setText(d)

    def _browse_file(self, target: QLineEdit) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Locate ffmpeg executable")
        if f:
            target.setText(f)

    def _open_download_dir(self) -> None:
        QDesktopServices.openUrl(f"file:///{self.dir_edit.text().replace(chr(92), '/')}")

    def _check_ffmpeg_async(self) -> None:
        QTimer.singleShot(300, self._check_ffmpeg)

    def _check_ffmpeg(self) -> None:
        ff, ver = ytdlp_service.check_runtime_deps(self.cfg.ffmpeg_path)
        self._ffmpeg = ff
        self.ffmpeg_badge.setText(("✓ " + ver[:60]) if ff else "⚠ FFmpeg missing")
        if hasattr(self, "s_ffver"):
            self.s_ffver.setText(ver)

    # -- fetch ------------------------------------------------------------
    def on_fetch(self) -> None:
        # If a fetch is already running, the button acts as Cancel (a blocked
        # network — e.g. firewall — must never leave the UI stuck).
        if self.fetch_thread is not None and self.fetch_thread.isRunning():
            self._fetch_gen += 1  # orphan the running thread; ignore its result
            old = self.fetch_thread
            self.fetch_thread = None
            # Never drop the last reference to a running QThread (Qt aborts
            # with "Destroyed while thread is still running").
            self._fetch_orphans.append(old)
            try:
                old.finished.connect(lambda _o=old: self._drop_fetch_orphan(_o))
            except Exception:
                pass
            self.fetch_btn.setText("Fetch")
            self.fetch_btn.setEnabled(True)
            self.status_label.setText("Fetch cancelled.")
            self._log("Fetch cancelled by user (a background attempt may still finish and will be ignored).")
            return
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "No link", "Paste a YouTube link first.")
            return
        if not is_valid_youtube_url(url):
            QMessageBox.warning(self, "Invalid link",
                                "That doesn't look like a YouTube video or playlist link.\n"
                                "Supported: youtube.com/watch, youtu.be, shorts/live/embed, playlist links.")
            return
        self._fetch_gen += 1
        gen = self._fetch_gen
        self.fetch_btn.setText("Cancel")
        self.fetch_btn.setEnabled(True)
        self.dl_btn.setEnabled(False)
        self.queue_btn.setEnabled(False)
        self.status_label.setText("Fetching media information… (press Cancel if this takes too long)")
        self._log(f"Fetching info for {url}")
        self.progress.setValue(0)
        self.fetch_thread = FetchThread(url, self.cfg.max_playlist_items)
        self.fetch_thread.done.connect(lambda info, _g=gen: self._on_fetched(info, _g))
        self.fetch_thread.failed.connect(lambda err, _g=gen: self._on_fetch_failed(err, _g))
        self.fetch_thread.start()

    def _drop_fetch_orphan(self, t) -> None:
        try:
            self._fetch_orphans.remove(t)
        except ValueError:
            pass
        try:
            t.deleteLater()
        except Exception:
            pass

    def _fetch_result_current(self, gen: int) -> bool:
        """Ignore results from orphaned (cancelled/superseded) fetch threads."""
        if gen != self._fetch_gen:
            self._log("Ignoring stale fetch result.")
            return False
        return True

    def _reset_fetch_btn(self) -> None:
        self.fetch_btn.setText("Fetch")
        self.fetch_btn.setEnabled(True)
        self.fetch_thread = None

    def _on_fetched(self, info: object, gen: int) -> None:
        if not self._fetch_result_current(gen):
            return
        self._reset_fetch_btn()
        if isinstance(info, VideoInfo):
            self.video, self.playlist = info, None
            self.playlist_list.hide()
            self._show_video(info)
            self._populate_quality(info)
            self.dl_btn.setEnabled(True)
            self.queue_btn.setEnabled(True)
            self.status_label.setText("Ready — pick quality and press Download.")
            self._log(f"Found: {info.title}")
        elif isinstance(info, PlaylistInfo):
            self.video, self.playlist = None, info
            self._show_playlist(info)
            self.dl_btn.setEnabled(True)
            self.queue_btn.setEnabled(True)
            self.status_label.setText(f"Playlist ready — {len(info.items)} items (showing up to {self.cfg.max_playlist_items}).")
            self._log(f"Playlist: {info.title} ({len(info.items)} items)")
        else:
            self._on_fetch_failed("Unexpected response from YouTube.", gen)

    def _on_fetch_failed(self, err: str, gen: int) -> None:
        if not self._fetch_result_current(gen):
            return
        self._reset_fetch_btn()
        self.status_label.setText("Fetch failed.")
        self._log(f"Fetch failed: {err}")
        QMessageBox.critical(
            self, "Could not read that link",
            f"{err}\n\nIf this keeps hanging instead of failing, your firewall may be "
            "blocking TubeFetch — allow it through Windows Firewall and retry.")

    def _show_video(self, v: VideoInfo) -> None:
        self.info_title.setText(v.title)
        meta = f"{v.uploader or 'Unknown uploader'}   •   {format_duration(v.duration)}   •   "
        meta += f"{v.view_count:,} views".replace(",", " ") if v.view_count else "views unknown"
        self.info_meta.setText(meta)
        self.thumb_label.setText("Loading thumbnail…")
        QTimer.singleShot(0, lambda: self._load_thumbnail(v.thumbnail))

    def _show_playlist(self, p: PlaylistInfo) -> None:
        self.info_title.setText(p.title or "Playlist")
        self.info_meta.setText(f"{p.count} videos • {p.uploader or ''}")
        self.playlist_list.clear()
        for it in p.items:
            QListWidgetItem(f"{it.index}. {it.title}  ({format_duration(it.duration)})", self.playlist_list)
        self.playlist_list.show()
        self.thumb_label.setText(f"📃 Playlist with {len(p.items)} items.\nPress Download to fetch each item\n(failures are skipped, not fatal).")
        # quality combos fall back to generic best
        self.quality_combo.clear()
        self.quality_combo.addItem("Best available", "best")
        self.quality_combo.addItem("Worst (smallest / fastest)", "worst")

    def _load_thumbnail(self, url: str | None) -> None:
        if not url:
            self.thumb_label.setText("(no thumbnail)")
            return
        try:
            import requests
            r = requests.get(url, timeout=12)
            r.raise_for_status()
            px = QPixmap()
            px.loadFromData(r.content)
            if not px.isNull():
                self.thumb_label.setPixmap(px.scaled(360, 203, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self.thumb_label.setText("(thumbnail unavailable)")

    def _populate_quality(self, v: VideoInfo) -> None:
        mode = self._current_mode()
        self._populating = True
        try:
            self.quality_combo.clear()
            self._quality_meta = []
            if mode == "video_audio":
                # Most YouTube videos now have NO combined streams (DASH separates
                # video and audio), so list each video track as "X + best audio".
                # A specific video id becomes "id+bestaudio/best" at download time.
                for o in v.combined:
                    self.quality_combo.addItem(o.short(), o.format_id)
                    self._quality_meta.append({"id": o.format_id, "height": o.height,
                                               "abr": None, "token": None})
                for o in v.video_only:
                    self.quality_combo.addItem(f"{o.short()} + best audio (auto-merge)", o.format_id)
                    self._quality_meta.append({"id": o.format_id, "height": o.height,
                                               "abr": None, "token": None})
                if not v.combined and not v.video_only:
                    self.quality_combo.addItem("Best available (auto-merge)", "best")
                    self._quality_meta.append({"id": "best", "height": None, "abr": None, "token": "best"})
                else:
                    self.quality_combo.addItem("Best available (auto-merge)", "best")
                    self._quality_meta.append({"id": "best", "height": None, "abr": None, "token": "best"})
                    self.quality_combo.addItem("Worst (smallest / fastest)", "worst")
                    self._quality_meta.append({"id": "worst", "height": None, "abr": None, "token": "worst"})
            elif mode == "video_only":
                for o in v.video_only or v.combined:
                    self.quality_combo.addItem(o.short(), o.format_id)
                    self._quality_meta.append({"id": o.format_id, "height": o.height,
                                               "abr": None, "token": None})
                self.quality_combo.addItem("Best video", "best")
                self._quality_meta.append({"id": "best", "height": None, "abr": None, "token": "best"})
                self.quality_combo.addItem("Worst video (smallest)", "worst")
                self._quality_meta.append({"id": "worst", "height": None, "abr": None, "token": "worst"})
            else:
                for o in v.audio_only:
                    self.quality_combo.addItem(o.short(), o.format_id)
                    self._quality_meta.append({"id": o.format_id, "height": None,
                                               "abr": o.abr or o.tbr, "token": None})
                self.quality_combo.addItem("Best audio", "best")
                self._quality_meta.append({"id": "best", "height": None, "abr": None, "token": "best"})
                self.quality_combo.addItem("Worst audio (smallest)", "worst")
                self._quality_meta.append({"id": "worst", "height": None, "abr": None, "token": "worst"})
        finally:
            self._populating = False
        if not self._apply_sticky_quality(mode):
            # No sticky preference yet: default to highest video entry, lowest audio.
            if mode in ("video_audio", "video_only") and self.quality_combo.count() >= 3:
                self.quality_combo.setCurrentIndex(self.quality_combo.count() - 3)
        self._apply_format_visibility()

    def _on_quality_changed(self, index: int) -> None:
        """Remember the user's pick so the next Fetch keeps the same preference."""
        if self._populating or index < 0 or index >= len(self._quality_meta):
            return
        meta = self._quality_meta[index]
        self._sticky[self._current_mode()] = dict(meta)

    def _remember_current_quality(self) -> None:
        idx = self.quality_combo.currentIndex()
        if 0 <= idx < len(self._quality_meta):
            self._sticky[self._current_mode()] = dict(self._quality_meta[idx])

    def _apply_sticky_quality(self, mode: str) -> bool:
        """Re-select the user's remembered preference in the fresh combo.
        Returns True if a sticky preference was applied."""
        pref = self._sticky.get(mode)
        if not pref:
            return False
        if pref.get("token") in ("best", "worst"):
            for i, m in enumerate(self._quality_meta):
                if m.get("token") == pref["token"]:
                    self.quality_combo.setCurrentIndex(i)
                    return True
            return False
        if mode == "audio_only" and pref.get("abr") is not None:
            best_i, best_abr = -1, -1.0
            for i, m in enumerate(self._quality_meta):
                a = m.get("abr")
                if a is not None and a <= pref["abr"] and a > best_abr:
                    best_i, best_abr = i, a
            if best_i >= 0:
                self.quality_combo.setCurrentIndex(best_i)
                return True
            # All options are higher-bitrate than preferred: take the smallest.
            for i, m in enumerate(self._quality_meta):
                if m.get("abr") is not None:
                    self.quality_combo.setCurrentIndex(i)
                    return True
            return False
        if pref.get("height") is not None:
            best_i, best_h = -1, -1
            for i, m in enumerate(self._quality_meta):
                h = m.get("height")
                if h is not None and h <= pref["height"] and h > best_h:
                    best_i, best_h = i, h
            if best_i >= 0:
                self.quality_combo.setCurrentIndex(best_i)
                return True
            # Everything is higher-res than preferred: take the smallest.
            for i, m in enumerate(self._quality_meta):
                if m.get("height") is not None:
                    self.quality_combo.setCurrentIndex(i)
                    return True
        return False

    def _current_mode(self) -> str:
        if self.rb_vonly.isChecked():
            return "video_only"
        if self.rb_aonly.isChecked():
            return "audio_only"
        return "video_audio"

    def _mode_changed(self) -> None:
        if self.video:
            self._populate_quality(self.video)
        else:
            self._apply_format_visibility()

    def _apply_format_visibility(self) -> None:
        mode = self._current_mode()
        is_audio = mode == "audio_only"
        self.vfmt_combo.setEnabled(not is_audio)
        self.afmt_combo.setEnabled(is_audio or mode == "video_audio")

    # -- download ----------------------------------------------------------
    def _build_request(self, url: str, title: str) -> DownloadRequest:
        qdata = self.quality_combo.currentData()
        return DownloadRequest(
            url=url,
            mode=self._current_mode(),
            quality=str(qdata or "best"),
            video_format=self.vfmt_combo.currentText(),
            audio_format=self.afmt_combo.currentText(),
            out_dir=self.dir_edit.text().strip() or self.cfg.download_dir,
            title=title or "youtube_download",
        )

    def on_download(self) -> None:
        if self.playlist:
            self._download_playlist()
            return
        if not self.video:
            return
        req = self._build_request(self.video.webpage_url, self.video.title)
        self._start_single(req)

    def _start_single(self, req: DownloadRequest) -> None:
        self.dl_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText("Starting download…")
        self._log(f"Downloading: {req.title} [{req.mode} → {req.video_format}/{req.audio_format}]")
        self.dl_thread = DownloadThread(req, self.cfg.ffmpeg_path or self._ffmpeg, self.cfg.max_retries)
        self.dl_thread.progress.connect(self._on_progress)
        self.dl_thread.finished_ok.connect(self._on_done)
        self.dl_thread.finished_fail.connect(self._on_failed)
        self.dl_thread.start()

    def _on_progress(self, p: DownloadProgress) -> None:
        if p.percent is not None:
            self.progress.setValue(int(max(0, min(100, p.percent))))
        if p.total_bytes:
            txt = f"{format_bytes(p.downloaded_bytes)} / {format_bytes(p.total_bytes)}"
        else:
            txt = f"{format_bytes(p.downloaded_bytes)} downloaded"
        extra = f"  •  {format_speed(p.speed_bps)}  •  ETA {format_eta(p.eta_secs)}"
        stage = {"merging": "Merging video+audio…", "converting": "Converting audio…"}.get(p.stage, "")
        self.status_label.setText(f"{stage + ' ' if stage else ''}{txt}{extra}")

    def _on_done(self, res) -> None:
        self.cancel_btn.setEnabled(False)
        self.dl_btn.setEnabled(True)
        self.progress.setValue(100)
        self.status_label.setText("✓ Download complete.")
        self._log(f"Saved → {res.filepath}")
        self._record_history(res.title or "download", res.filepath or "", "done")
        QMessageBox.information(self, "Download complete", f"Saved to:\n{res.filepath}")

    def _on_failed(self, res) -> None:
        self.cancel_btn.setEnabled(False)
        self.dl_btn.setEnabled(True)
        self.status_label.setText("Download failed / cancelled.")
        self._log(f"Failed: {res.error}")
        self._record_history(res.title or "download", "", "failed")
        if (res.error or "") == "Cancelled by user.":
            QMessageBox.information(self, "Cancelled", "Download was cancelled. Partial files were cleaned up.")
        else:
            QMessageBox.critical(self, "Download failed", res.error or "Download failed.")

    def on_cancel(self) -> None:
        self.playlist_cancel = True
        if self.dl_thread:
            self.dl_thread.cancel()
            self.status_label.setText("Cancelling…")
            self._log("Cancel requested by user.")

    # -- playlist ------------------------------------------------------------
    def _download_playlist(self) -> None:
        assert self.playlist
        items = self.playlist.items
        if not items:
            return
        self.playlist_cancel = False
        self.dl_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._log(f"Starting playlist: {len(items)} items")
        QTimer.singleShot(0, lambda: self._dl_playlist_next(items, 0, [], []))

    def _dl_playlist_next(self, items, idx: int, ok_files: list, failed: list) -> None:
        if self.playlist_cancel:
            self.cancel_btn.setEnabled(False)
            self.dl_btn.setEnabled(True)
            self.status_label.setText(f"Playlist cancelled ({idx}/{len(items)} done).")
            self._log("Playlist cancelled by user.")
            return
        if idx >= len(items):
            self.cancel_btn.setEnabled(False)
            self.dl_btn.setEnabled(True)
            self.progress.setValue(100)
            self.status_label.setText(f"Playlist done: {len(ok_files)} ok, {len(failed)} failed.")
            self._log(f"Playlist done: {len(ok_files)} ok, {len(failed)} failed.")
            for t, p in ok_files:
                self._record_history(t, p, "done")
            for t, e in failed:
                self._record_history(t, "", f"failed: {e}")
            QMessageBox.information(self, "Playlist finished",
                                    f"Completed {len(ok_files)}/{len(items)}.\n"
                                    + (f"Skipped {len(failed)} failed item(s)." if failed else "All items downloaded."))
            return
        it = items[idx]
        self.status_label.setText(f"Item {idx+1}/{len(items)}: {it.title}")
        self._log(f"[{idx+1}/{len(items)}] {it.title}")
        req = self._build_request(it.url, it.title)
        self.dl_thread = DownloadThread(req, self.cfg.ffmpeg_path or self._ffmpeg, self.cfg.max_retries)
        self.dl_thread.progress.connect(self._on_progress)

        def _ok(res, _idx=idx, _it=it):
            ok_files.append((_it.title, res.filepath or ""))
            self._dl_playlist_next(items, _idx + 1, ok_files, failed)

        def _fail(res, _idx=idx, _it=it):
            if (res.error or "") == "Cancelled by user.":
                self._dl_playlist_next(items, len(items), ok_files, failed)
                self.status_label.setText("Playlist cancelled.")
                self.cancel_btn.setEnabled(False)
                self.dl_btn.setEnabled(True)
                return
            failed.append((_it.title, res.error or "failed"))
            self._log(f"Skipping failed item: {_it.title} — {res.error}")
            self._dl_playlist_next(items, _idx + 1, ok_files, failed)

        self.dl_thread.finished_ok.connect(_ok)
        self.dl_thread.finished_fail.connect(_fail)
        self.dl_thread.start()

    # -- queue ---------------------------------------------------------------
    def on_add_queue(self) -> None:
        url = self.url_edit.text().strip()
        title = self.video.title if self.video else (self.playlist.title or url if self.playlist else url)
        self._remember_current_quality()
        qd = self.quality_combo.currentData() or "best"
        self.queue.append(QueueEntry(
            url=url, title=title, mode=self._current_mode(), quality=str(qd),
            quality_label=self.quality_combo.currentText(),
            video_format=self.vfmt_combo.currentText(),
            audio_format=self.afmt_combo.currentText(),
            out_dir=self.dir_edit.text().strip() or self.cfg.download_dir,
        ))
        self._refresh_queue()
        self._log(f"Queued: {title} [{self.quality_combo.currentText()}]")

    def _refresh_queue(self) -> None:
        self.queue_list.clear()
        for e in self.queue:
            label = f"[{e.mode}] {e.title}"
            if e.quality_label:
                label += f"  —  {e.quality_label}"
            QListWidgetItem(label, self.queue_list)

    def on_remove_selected(self) -> None:
        for item in self.queue_list.selectedItems():
            row = self.queue_list.row(item)
            self.queue.pop(row)
        self._refresh_queue()

    def on_start_queue(self) -> None:
        if not self.queue:
            QMessageBox.information(self, "Queue empty", "Add some items from the Download tab first.")
            return
        self.playlist_cancel = False
        entries = list(self.queue)
        self.queue.clear()
        self._refresh_queue()
        self.tabs.setCurrentIndex(0)
        QTimer.singleShot(0, lambda: self._run_queue(entries, 0, 0, 0))

    def _run_queue(self, entries: list[QueueEntry], idx: int, ok: int, fail: int) -> None:
        if self.playlist_cancel or idx >= len(entries):
            self.cancel_btn.setEnabled(False)
            self.dl_btn.setEnabled(True)
            self.q_status.setText(f"Queue finished: {ok} ok, {fail} failed.")
            return
        e = entries[idx]
        # Each entry carries the quality/container/folder locked in when it was
        # added — never the picker's current values, so item 2+ can't silently
        # inherit "best" or a different folder.
        req = DownloadRequest(url=e.url, mode=e.mode, quality=e.quality,
                              video_format=e.video_format or self.vfmt_combo.currentText(),
                              audio_format=e.audio_format or self.afmt_combo.currentText(),
                              out_dir=e.out_dir or self.dir_edit.text().strip() or self.cfg.download_dir,
                              title=e.title)
        self.q_status.setText(f"Queue item {idx+1}/{len(entries)}: {e.title}")
        self.dl_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText(f"Queue {idx+1}/{len(entries)}: {e.title}")
        self._log(f"Queue [{idx+1}/{len(entries)}]: {e.title}")
        self.dl_thread = DownloadThread(req, self.cfg.ffmpeg_path or self._ffmpeg, self.cfg.max_retries)
        self.dl_thread.progress.connect(self._on_progress)

        def _next_ok(res, _i=idx):
            self._record_history(res.title, res.filepath or "", "done")
            self.progress.setValue(100)
            self._run_queue(entries, _i + 1, ok + 1, fail)

        def _next_fail(res, _i=idx):
            if (res.error or "") == "Cancelled by user.":
                self.q_status.setText("Queue cancelled.")
                return
            self._record_history(res.title, "", "failed")
            self._run_queue(entries, _i + 1, ok, fail + 1)
        try:
            self.dl_thread.finished_ok.disconnect()
        except Exception:
            pass
        try:
            self.dl_thread.finished_fail.disconnect()
        except Exception:
            pass
        self.dl_thread.finished_ok.connect(_next_ok)
        self.dl_thread.finished_fail.connect(_next_fail)
        self.dl_thread.start()

    # -- history ---------------------------------------------------------------
    def _record_history(self, title: str, path: str, status: str) -> None:
        if not self.cfg.remember_history:
            return
        self.history.add({"when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                          "title": title, "path": path, "status": status})
        self._refresh_history_table()

    def _refresh_history_table(self) -> None:
        rows = self.history.entries[::-1][:100]
        self.hist_table.setRowCount(len(rows))
        for i, e in enumerate(rows):
            for j, k in enumerate(("when", "title", "path", "status")):
                self.hist_table.setItem(i, j, QTableWidgetItem(str(e.get(k, ""))))

    # -- settings ---------------------------------------------------------------
    def _refresh_settings_ui(self) -> None:
        self.s_dir.setText(self.cfg.download_dir)
        self.s_mode.setCurrentText(self.cfg.mode if self.cfg.mode in ("video_audio", "video_only", "audio_only") else "video_audio")
        self.s_vfmt.setCurrentText(self.cfg.video_format)
        self.s_afmt.setCurrentText(self.cfg.audio_format)
        self.s_retries.setValue(self.cfg.max_retries)
        self.s_maxpl.setValue(self.cfg.max_playlist_items)
        self.s_ffmpeg.setText(self.cfg.ffmpeg_path)
        if self.cfg.mode == "video_only":
            self.rb_vonly.setChecked(True)
        elif self.cfg.mode == "audio_only":
            self.rb_aonly.setChecked(True)
        else:
            self.rb_both.setChecked(True)
        self.vfmt_combo.setCurrentText(self.cfg.video_format)
        self.afmt_combo.setCurrentText(self.cfg.audio_format)

    def _save_settings_from_ui(self) -> None:
        self.cfg.download_dir = self.s_dir.text().strip() or self.cfg.download_dir
        self.cfg.mode = self.s_mode.currentText()
        self.cfg.video_format = self.s_vfmt.currentText()
        self.cfg.audio_format = self.s_afmt.currentText()
        self.cfg.max_retries = self.s_retries.value()
        self.cfg.max_playlist_items = self.s_maxpl.value()
        self.cfg.ffmpeg_path = self.s_ffmpeg.text().strip()
        self.cfg_mgr.save()
        self.dir_edit.setText(self.cfg.download_dir)
        self.vfmt_combo.setCurrentText(self.cfg.video_format)
        self.afmt_combo.setCurrentText(self.cfg.audio_format)
        self._check_ffmpeg()
        self._log("Settings saved.")
        QMessageBox.information(self, "Settings", "Preferences saved.")


def run_app() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("TubeFetch")
    app.setOrganizationName("TubeFetch")
    cfg_mgr = ConfigManager()
    hist = HistoryStore()
    win = MainWindow(cfg_mgr, hist)
    win.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(run_app())
    except Exception:
        traceback.print_exc()
        raise
