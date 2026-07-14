from __future__ import annotations

from bkwhisperx.diagnostics import clear_caches, directory_size


def test_download_cache_can_be_measured_and_cleared(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BKWHISPERX_HOME", str(tmp_path / "app"))
    cache = tmp_path / "app" / "cache"
    cache.mkdir(parents=True)
    (cache / "package.bin").write_bytes(b"x" * 128)
    assert directory_size(cache) == 128
    cleared = clear_caches(["downloads"])
    assert cleared == {"downloads": 128}
    assert cache.is_dir()
    assert list(cache.iterdir()) == []
