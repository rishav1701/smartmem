import asyncio
from types import SimpleNamespace

from services import ollama_runtime as runtime


def test_auto_start_endpoint_must_be_loopback(monkeypatch):
    monkeypatch.setattr(runtime, "OLLAMA_BASE", "http://192.168.1.20:11434")
    assert runtime._local_ollama_host() is None
    monkeypatch.setattr(runtime, "OLLAMA_BASE", "http://localhost:11434")
    assert runtime._local_ollama_host() == ("127.0.0.1", 11434)


def test_auto_start_rejects_untrusted_url_shapes(monkeypatch):
    for url in (
        "https://localhost:11434",
        "http://user:password@127.0.0.1:11434",
        "http://localhost:11434/proxy",
        "http://localhost.evil.example:11434",
    ):
        monkeypatch.setattr(runtime, "OLLAMA_BASE", url)
        assert runtime._local_ollama_host() is None


def test_auto_start_does_not_spawn_for_remote_endpoint(monkeypatch):
    monkeypatch.setattr(runtime, "ENABLE_OLLAMA", True)
    monkeypatch.setattr(runtime, "OLLAMA_BASE", "http://192.168.1.20:11434")
    monkeypatch.setattr(runtime, "_is_running", _async_false)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("must not launch a process for a remote Ollama endpoint")
    ))

    assert asyncio.run(runtime.ensure_ollama_started()) == "non_local_endpoint"


def test_auto_start_uses_direct_loopback_process_launch(monkeypatch):
    monkeypatch.setattr(runtime, "ENABLE_OLLAMA", True)
    monkeypatch.setattr(runtime, "OLLAMA_AUTOSTART", True)
    monkeypatch.setattr(runtime, "OLLAMA_BASE", "http://localhost:11434")
    monkeypatch.setattr(runtime, "_START_ATTEMPTED", False)
    monkeypatch.setattr(runtime, "_find_ollama", lambda: "C:/Ollama/ollama.exe")
    probes = iter((False, False, True))
    monkeypatch.setattr(runtime, "_is_running", lambda: _async_next(probes))
    launched = {}

    def fake_popen(args, **kwargs):
        launched["args"] = args
        launched["kwargs"] = kwargs
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)
    assert asyncio.run(runtime.ensure_ollama_started()) == "started"
    assert launched["args"] == ["C:/Ollama/ollama.exe", "serve"]
    assert "shell" not in launched["kwargs"]
    assert launched["kwargs"]["env"]["OLLAMA_HOST"] == "127.0.0.1:11434"


async def _async_false():
    return False


async def _async_next(values):
    return next(values)
