from __future__ import annotations

from pathlib import Path

from bkwhisperx.paths import runtime_environment


def test_existing_legacy_model_caches_are_reused(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    legacy_hf = home / ".cache" / "huggingface"
    legacy_torch = home / ".cache" / "torch"
    legacy_hf.mkdir(parents=True)
    legacy_torch.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BKWHISPERX_HOME", str(tmp_path / "app"))
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.delenv("TORCH_HOME", raising=False)

    env = runtime_environment()
    assert Path(env["HF_HOME"]) == legacy_hf
    assert Path(env["HUGGINGFACE_HUB_CACHE"]) == legacy_hf / "hub"
    assert Path(env["TORCH_HOME"]) == legacy_torch
