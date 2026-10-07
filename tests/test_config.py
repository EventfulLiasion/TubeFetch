"""Config persistence tests (isolated temp paths)."""
from app.config import AppConfig, ConfigManager, HistoryStore


def test_config_roundtrip(tmp_path):
    p = tmp_path / "settings.json"
    m = ConfigManager(path=p)
    m.config.download_dir = str(tmp_path / "dl")
    m.config.audio_format = "m4a"
    m.save()
    m2 = ConfigManager(path=p)
    assert m2.config.audio_format == "m4a"


def test_config_corrupt_falls_back(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not json", encoding="utf-8")
    m = ConfigManager(path=p)
    assert isinstance(m.config, AppConfig)


def test_history_add_and_cap(tmp_path):
    h = HistoryStore(path=tmp_path / "h.json")
    for i in range(5):
        h.add({"title": f"v{i}"})
    assert len(h.entries) == 5
    h.clear()
    assert h.entries == []


def test_relative_download_dir_resolves_inside_project(tmp_path):
    from app import logger

    assert logger.resolve_download_dir("my-downloads") == logger.project_root() / "my-downloads"


def test_absolute_download_dir_untouched():
    from app import logger

    p = logger.resolve_download_dir("C:/Temp/Somewhere")
    assert p.is_absolute() and str(p).endswith("Somewhere")
