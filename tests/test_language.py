from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bkwhisperx.config import TranscriptionConfig
from bkwhisperx.engine import load_whisperx_model, resolve_device_and_defaults


class ModelWithRuntimeLanguage:
    def transcribe(self, audio, *, batch_size=1, chunk_size=30, language=None, task="transcribe", print_progress=False):
        return {"segments": [], "language": language}


def config(language: str = "fa") -> TranscriptionConfig:
    return TranscriptionConfig(
        audio_files=[str(Path(__file__))],
        output_dir=str(Path(__file__).parent),
        language=language,
        task="transcribe",
    )


def test_manual_language_is_passed_when_loading_model() -> None:
    calls: dict[str, object] = {}

    def load_model(model_name, device, *, compute_type="auto", language=None, task="transcribe", **kwargs):
        calls.update(model=model_name, device=device, language=language, task=task, kwargs=kwargs)
        return ModelWithRuntimeLanguage()

    model, language_at_load, task_at_load = load_whisperx_model(
        SimpleNamespace(load_model=load_model), config("fa"), "cpu", "int8", lambda _line: None
    )
    assert isinstance(model, ModelWithRuntimeLanguage)
    assert calls["language"] == "fa"
    assert calls["task"] == "transcribe"
    assert language_at_load is True
    assert task_at_load is True


def test_auto_language_is_not_forced_at_model_load() -> None:
    calls: dict[str, object] = {}

    def load_model(model_name, device, *, compute_type="auto", language=None, task="transcribe"):
        calls["language"] = language
        return ModelWithRuntimeLanguage()

    _, language_at_load, _ = load_whisperx_model(
        SimpleNamespace(load_model=load_model), config("auto"), "cpu", "int8", lambda _line: None
    )
    assert calls["language"] is None
    assert language_at_load is False


def test_loader_never_silently_drops_manual_language() -> None:
    class ModelWithoutLanguage:
        def transcribe(self, audio, *, batch_size=1):
            return {"segments": []}

    def load_model(model_name, device, *, compute_type="auto"):
        return ModelWithoutLanguage()

    with pytest.raises(RuntimeError, match="cannot enforce language='fa'"):
        load_whisperx_model(SimpleNamespace(load_model=load_model), config("fa"), "cpu", "int8", lambda _line: None)


def test_cpu_float16_is_rejected() -> None:
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    cfg = config("fa")
    cfg.device = "cpu"
    cfg.compute_type = "float16"
    with pytest.raises(ValueError, match="float16 is not supported"):
        resolve_device_and_defaults(cfg, fake_torch)


def test_prompt_and_hotwords_reach_model_options() -> None:
    calls: dict[str, object] = {}

    def load_model(model_name, device, *, asr_options=None, **kwargs):
        calls["asr_options"] = asr_options
        return ModelWithRuntimeLanguage()

    cfg = config("fa")
    cfg.initial_prompt = "نام: کیارستمی"
    cfg.hotwords = "کیارستمی, سینما"
    load_whisperx_model(SimpleNamespace(load_model=load_model), cfg, "cpu", "int8", lambda _line: None)
    options = calls["asr_options"]
    assert isinstance(options, dict)
    assert options["initial_prompt"] == "نام: کیارستمی"
    assert options["hotwords"] == "کیارستمی, سینما"


def test_performance_preset_scales_automatic_gpu_batch() -> None:
    cuda = SimpleNamespace(
        is_available=lambda: True,
        get_device_properties=lambda _index: SimpleNamespace(total_memory=12 * 1024**3),
    )
    fake_torch = SimpleNamespace(cuda=cuda)
    safe = config()
    safe.device = "cuda"
    safe.performance_preset = "safe"
    maximum = config()
    maximum.device = "cuda"
    maximum.performance_preset = "maximum"
    assert resolve_device_and_defaults(safe, fake_torch)[2] == 8
    assert resolve_device_and_defaults(maximum, fake_torch)[2] == 24
