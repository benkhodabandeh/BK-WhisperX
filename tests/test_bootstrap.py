from __future__ import annotations

import bkwhisperx.bootstrap as bootstrap


def test_packaged_runtime_requirements_are_available() -> None:
    requirements = bootstrap.requirements_path()
    assert requirements.parent.name == "bkwhisperx"
    assert requirements.is_file()


def test_automatic_profile_selects_cuda_128_for_current_windows_driver(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "detect_nvidia", lambda: (True, "GPU", "572.61"))
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    assert bootstrap.automatic_profile() == "cuda128"


def test_automatic_profile_selects_cuda_126_for_compatible_windows_driver(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "detect_nvidia", lambda: (True, "GPU", "561.17"))
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    assert bootstrap.automatic_profile() == "cuda126"


def test_automatic_profile_uses_cpu_for_old_driver(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "detect_nvidia", lambda: (True, "GPU", "552.44"))
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    assert bootstrap.automatic_profile() == "cpu"
