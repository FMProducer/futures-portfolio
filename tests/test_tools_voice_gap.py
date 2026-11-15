"""Offline coverage for tools/voice_recognizer.py with a fake vosk (not installed here)."""
import importlib.util
import json
import sys
import types
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / 'tools'


class _FakeRecognizer:
    def __init__(self, model, rate):
        self.rate = rate
        self.words = False
        self.frames = 0

    def SetWords(self, value):
        self.words = value

    def AcceptWaveData(self, data):
        self.frames += len(data)

    def FinalResult(self):
        return json.dumps({'text': 'privet mir', 'result': []})


class _FakeModel:
    def __init__(self, path=None):
        self.path = path


@pytest.fixture
def voice(monkeypatch, tmp_path):
    """Load tools/voice_recognizer.py with a fake vosk and a fake ffmpeg subprocess."""
    fake_vosk = types.ModuleType('vosk')
    fake_vosk.Model = _FakeModel
    fake_vosk.KaldiRecognizer = _FakeRecognizer
    monkeypatch.setitem(sys.modules, 'vosk', fake_vosk)

    import subprocess
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)

        class Result:
            returncode = 0
            stdout = ''
            stderr = 'ffmpeg ok'

        return Result()

    monkeypatch.setattr(subprocess, 'run', fake_run)
    spec = importlib.util.spec_from_file_location('voice_recognizer', TOOLS / 'voice_recognizer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FFMPEG_PATH = str(tmp_path / 'ffmpeg.exe')
    module.MODEL_PATH = str(tmp_path / 'vosk-model')
    module._model = _FakeModel(module.MODEL_PATH)  # avoid re-creating through _get_model
    return module, calls


def _write_wav(path, rate=16000, channels=1, sampwidth=2):
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(b'\x00\x00' * 16000)


def test_vosk_model_cache(voice):
    module, _ = voice
    assert module._get_model() is module._model


def test_ogg_to_wav_runs_ffmpeg(voice):
    module, calls = voice
    out = module.ogg_to_wav('in.ogg', 'out.wav')
    assert out == 'out.wav'
    assert calls and calls[0][0] == str(module.FFMPEG_PATH)


def test_ogg_to_wav_reports_ffmpeg_failure(monkeypatch, voice):
    module, _ = voice
    import subprocess

    def broken(cmd, *args, **kwargs):
        class Result:
            returncode = 1
            stdout = ''
            stderr = 'no such input'

        return Result()

    monkeypatch.setattr(subprocess, 'run', broken)
    with pytest.raises(RuntimeError, match='ffmpeg failed'):
        module.ogg_to_wav('in.ogg', 'out.wav')


def test_recognize_wav_reads_a_valid_stream(voice, tmp_path):
    module, _ = voice
    wav = tmp_path / 'good.wav'
    _write_wav(wav)
    text = module.recognize_wav(str(wav))
    assert text == 'privet mir'


def test_recognize_wav_rejects_a_wrong_format(voice, tmp_path):
    module, _ = voice
    bad = tmp_path / 'bad.wav'
    _write_wav(bad, rate=8000)
    with pytest.raises(ValueError, match='16kHz'):
        module.recognize_wav(str(bad))


def test_recognize_voice_runs_the_pipeline_and_cleans_up(voice, tmp_path):
    module, _ = voice
    wav = tmp_path / 'conv.wav'
    _write_wav(wav)
    result = module.recognize_voice('voice.ogg', str(wav))
    assert result == 'privet mir'
    assert not wav.exists()  # finally-block cleanup


def test_recognize_voice_auto_wav_path_and_missing_wav_error(voice, tmp_path):
    module, _ = voice
    auto = tmp_path / 'voice.ogg'
    auto.write_text('not really ogg', encoding='utf-8')
    target = tmp_path / 'voice_converted.wav'
    _write_wav(target)
    result = module.recognize_voice(str(auto))
    assert result == 'privet mir'
    assert not target.exists()
