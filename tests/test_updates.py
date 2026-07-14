from bkwhisperx.updates import UpdateInfo, check_for_update_cached, is_newer


def test_version_comparison() -> None:
    assert is_newer("v1.2.0", "1.1.9")
    assert is_newer("2.0", "1.99.99")
    assert not is_newer("v1.1.0", "1.1.0")
    assert not is_newer("1.0.9", "1.1.0")


def test_update_check_uses_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BKWHISPERX_HOME", str(tmp_path / "app"))
    calls = 0

    def fake_check(current_version: str) -> UpdateInfo:
        nonlocal calls
        calls += 1
        return UpdateInfo(False, current_version, latest_version="1.1.0")

    monkeypatch.setattr("bkwhisperx.updates.check_for_update", fake_check)
    first = check_for_update_cached("1.1.0")
    second = check_for_update_cached("1.1.0")
    assert first.latest_version == second.latest_version == "1.1.0"
    assert calls == 1
