"""TTS 高速・自然日本語音声合成エンジンの単体テスト (Gemini TTS 対応)."""

import base64
import io
import time
import wave
from unittest.mock import MagicMock, patch

import pytest

import app.tts as tts_module
from app.tts import (
    _TTS_CACHE,
    _cloud_tts_endpoint,
    _synthesize_with_gemini_tts,
    _to_playable_audio,
    clean_markdown_for_speech,
    extract_natural_speech_script,
    pcm_to_wav,
    synthesize_speech,
)


@pytest.fixture(autouse=True)
def _reset_tts_state():
    """テスト間でキャッシュ・成功/失敗モデルの記憶をリセットする."""
    _TTS_CACHE.clear()
    tts_module._FAILED_TTS_MODELS.clear()
    tts_module._PREFERRED_TTS_MODEL = None
    yield
    _TTS_CACHE.clear()
    tts_module._FAILED_TTS_MODELS.clear()
    tts_module._PREFERRED_TTS_MODEL = None


def test_pcm_to_wav_is_playable_24khz():
    pcm = b"\x00\x00" * 2400  # 0.1 秒の無音
    wav_bytes = pcm_to_wav(pcm, sample_rate=24000)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getframerate() == 24000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == 2400


def test_to_playable_audio_wraps_l16_pcm_as_wav():
    b64, mime = _to_playable_audio(b"\x00\x00" * 10, "audio/L16;codec=pcm;rate=24000")
    assert mime == "audio/wav"
    assert base64.b64decode(b64)[:4] == b"RIFF"
    # MP3 などはそのまま
    b64_mp3, mime_mp3 = _to_playable_audio(b"ID3", "audio/mpeg")
    assert mime_mp3 == "audio/mpeg"
    assert base64.b64decode(b64_mp3) == b"ID3"


def test_cloud_tts_endpoint_by_region():
    assert _cloud_tts_endpoint("global") == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert _cloud_tts_endpoint("us") == "https://us-texttospeech.googleapis.com/v1/text:synthesize"


def test_default_tts_region_and_chain():
    assert tts_module.TTS_LOCATION == "global"
    assert tts_module.TTS_MODEL_CHAIN[-1] == "gemini-3.1-flash-tts-preview"


def test_tts_falls_through_to_next_model_and_remembers_success():
    sample = "【対応指示】\nPOS-01 を再起動してください。"
    ok = {"status": "success", "audio_content": "eA==", "mime_type": "audio/wav",
          "speech_text": "x", "voice": "gemini-3.1-flash-tts-preview (Gemini TTS / global)"}

    def fake(text, model_name, voice_name=None):
        return ok if model_name == "gemini-3.1-flash-tts-preview" else None

    with patch("app.tts._synthesize_with_gemini_tts", side_effect=fake) as mock_gemini:
        res = synthesize_speech(sample)
        assert res["voice"].startswith("gemini-3.1-flash-tts-preview")
        called = [c.kwargs["model_name"] for c in mock_gemini.call_args_list]
        assert called == tts_module.TTS_MODEL_CHAIN

        # 2回目 (別テキスト): 成功モデルを最優先、失敗モデルはスキップされる
        mock_gemini.reset_mock()
        synthesize_speech("【対応指示】\nPOS-02 を確認してください。")
        called2 = [c.kwargs["model_name"] for c in mock_gemini.call_args_list]
        assert called2 == ["gemini-3.1-flash-tts-preview"]


def test_gemini_tts_uses_global_region_client_on_vertex(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")

    part = MagicMock()
    part.inline_data.data = b"\x00\x00" * 10
    part.inline_data.mime_type = "audio/L16;codec=pcm;rate=24000"
    response = MagicMock()
    response.candidates = [MagicMock(content=MagicMock(parts=[part]))]

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.return_value = response
        res = _synthesize_with_gemini_tts("テスト", model_name="gemini-3.1-flash-tts-preview")

    kwargs = mock_client_cls.call_args.kwargs
    assert kwargs["vertexai"] is True
    assert kwargs["location"] == "global"
    assert res["mime_type"] == "audio/wav"
    config = mock_client_cls.return_value.models.generate_content.call_args.kwargs["config"]
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"


def test_clean_markdown_for_speech():
    raw = "【対応指示】\n- **GATE-AP-02** のPoEポート再起動\n詳細は https://runbook.internal/poe を参照。"
    cleaned = clean_markdown_for_speech(raw)
    assert "https://" not in cleaned
    assert "**" not in cleaned
    assert "GATE-AP-02" in cleaned


def test_extract_natural_speech_script_quality_and_speed():
    sample_text = (
        "【対応指示】\n"
        "1. GATE-AP-02 の PoE ポートを再起動してください。\n"
        "2. ERR-GATE-408 のログクリアを確認してください。\n\n"
        "【原因・理由】\n"
        "デリカコーナーのアクセスポイント GATE-AP-02 でセッション切断が発生しているため。\n\n"
        "【解決しない場合】\n"
        "ネットワーク専任チームへエスカレーションしてください。"
    )

    t0 = time.perf_counter()
    plain, ssml = extract_natural_speech_script(sample_text)
    duration_ms = (time.perf_counter() - t0) * 1000

    # 1. 速度検証: 20ミリ秒以内で完了すること
    assert duration_ms < 20.0, f"Extraction took too long: {duration_ms}ms"

    # 2. 自然な日本語読み替え検証
    assert "ゲート・エーピー・ゼロツー" in plain
    assert "エラー・ゲート・よんまるはち" in plain
    assert "対応手順をお伝えします。" in plain
    assert "原因は、" in plain
    assert "詳しい手順や画面ログは、端末画面をご確認ください。" in plain

    # 3. SSML 構文検証
    assert ssml.startswith("<speak>")
    assert ssml.endswith("</speak>")
    assert '<break time="250ms"/>' in ssml


def test_gemini_flash_lite_tts_priority(monkeypatch):
    # .env の TTS_MODEL_CHAIN に依存せず、コード既定チェーンの優先順を検証する
    monkeypatch.setattr(
        tts_module,
        "TTS_MODEL_CHAIN",
        [
            "gemini-3.8-flash-lite-tts",
            "gemini-3.8-flash-tts",
            "gemini-3.1-flash-tts-preview",
        ],
    )
    _TTS_CACHE.clear()
    sample = "【対応指示】\nPOS-01 を再起動してください。"

    mock_gemini_result = {
        "status": "success",
        "audio_content": "Z2VtaW5pX2ZsYXNoX2xpdGVfdHRz",
        "mime_type": "audio/wav",
        "speech_text": "対応手順をお伝えします。ピーオーエス・ゼロワンを再起動してください。",
        "voice": "gemini-3.8-flash-lite-tts (Gemini AI Native Audio)",
    }

    with patch("app.tts._synthesize_with_gemini_tts", return_value=mock_gemini_result) as mock_gemini:
        res = synthesize_speech(sample)
        assert res["status"] == "success"
        assert "gemini-3.8-flash-lite-tts" in res["voice"]
        assert res["audio_content"] == "Z2VtaW5pX2ZsYXNoX2xpdGVfdHRz"
        # gemini-3.8-flash-lite-tts が最優先で呼ばれたことを確認
        assert mock_gemini.call_args_list[0][1]["model_name"] == "gemini-3.8-flash-lite-tts"


def test_tts_cache_mechanism_and_neural2_fallback():
    _TTS_CACHE.clear()
    sample = "【対応指示】\nPOS-01 を再起動してください。"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"audioContent": "bW9ja19hdWRpb19kYXRh"}

    # Gemini-TTS が None を返した場合は Neural2 へフォールバック
    with patch("app.tts._synthesize_with_gemini_tts", return_value=None), \
         patch("google.auth.default", return_value=(MagicMock(), "test-proj")), \
         patch("requests.post", return_value=mock_resp) as mock_post:
        # 1回目の呼び出し
        res1 = synthesize_speech(sample)
        assert res1["status"] == "success"
        assert res1["audio_content"] == "bW9ja19hdWRpb19kYXRh"
        assert "Neural2" in res1["voice"]
        assert mock_post.call_count == 1

        # 2回目の呼び出し（キャッシュヒット -> requests.post は呼ばれない）
        res2 = synthesize_speech(sample)
        assert res2["status"] == "success"
        assert res2["audio_content"] == "bW9ja19hdWRpb19kYXRh"
        assert mock_post.call_count == 1


def test_empty_text():
    res = synthesize_speech("   ")
    assert res["status"] == "error"


# ---------------------------------------------------------------------
# エージェントごとの固定ボイス
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "agent_id, gemini_voice, fallback_voice",
    [
        ("aiops_orchestrator", "Kore", "ja-JP-Neural2-B"),
        ("infra_specialist", "Charon", "ja-JP-Neural2-D"),
        ("order_specialist", "Leda", "ja-JP-Wavenet-A"),
        ("network_specialist", "Puck", "ja-JP-Neural2-C"),
        ("pos_specialist", "Aoede", "ja-JP-Wavenet-B"),
        (None, "Kore", "ja-JP-Neural2-B"),
        ("unknown_agent", "Kore", "ja-JP-Neural2-B"),
    ],
)
def test_resolve_agent_voice(agent_id, gemini_voice, fallback_voice):
    assert tts_module.resolve_agent_voice(agent_id) == (gemini_voice, fallback_voice)


def test_five_agents_have_distinct_voices():
    gemini = [v["gemini"] for v in tts_module.AGENT_VOICES.values()]
    fallback = [v["fallback"] for v in tts_module.AGENT_VOICES.values()]
    assert len(tts_module.AGENT_VOICES) == 5
    assert len(set(gemini)) == 5
    assert len(set(fallback)) == 5


def test_synthesize_speech_passes_agent_voice_and_separates_cache():
    sample = "【対応指示】\nPOS-01 を再起動してください。"
    ok = {"status": "success", "audio_content": "eA==", "mime_type": "audio/wav", "speech_text": "x", "voice": "v"}

    with patch("app.tts._synthesize_with_gemini_tts", return_value=ok) as mock_gemini:
        synthesize_speech(sample, agent_id="infra_specialist")
        assert mock_gemini.call_args.kwargs["voice_name"] == "Charon"

        # 同じ文章でも別エージェントならキャッシュを使わず別の声で合成する
        synthesize_speech(sample, agent_id="pos_specialist")
        assert mock_gemini.call_count == 2
        assert mock_gemini.call_args.kwargs["voice_name"] == "Aoede"

        # 同じエージェント・同じ文章はキャッシュヒット
        synthesize_speech(sample, agent_id="pos_specialist")
        assert mock_gemini.call_count == 2


def test_fallback_uses_agent_specific_cloud_voice():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"audioContent": "bW9jaw=="}

    with patch("app.tts._synthesize_with_gemini_tts", return_value=None), \
         patch("google.auth.default", return_value=(MagicMock(), "test-proj")), \
         patch("requests.post", return_value=mock_resp) as mock_post:
        res = synthesize_speech("【対応指示】\nテストです。", agent_id="network_specialist")
    assert mock_post.call_args.kwargs["json"]["voice"]["name"] == "ja-JP-Neural2-C"
    assert "ja-JP-Neural2-C" in res["voice"]


def test_tts_endpoint_accepts_agent_id():
    from fastapi.testclient import TestClient

    from app.fast_api_app import app

    ok = {"status": "success", "audio_content": "eA==", "mime_type": "audio/wav", "speech_text": "x", "voice": "v"}
    with patch("app.tts.synthesize_speech", return_value=ok) as mock_synth:
        res = TestClient(app).post("/api/tts", json={"text": "テスト", "agent_id": "order_specialist"})
    assert res.status_code == 200
    mock_synth.assert_called_once_with("テスト", agent_id="order_specialist")


# --- ストリーミング TTS (/api/tts/stream) ---


def _stream_chunk(data: bytes) -> MagicMock:
    part = MagicMock()
    part.inline_data.data = data
    chunk = MagicMock()
    chunk.candidates = [MagicMock(content=MagicMock(parts=[part]))]
    return chunk


def test_stream_speech_pcm_yields_chunks_with_agent_voice():
    from app.tts import stream_speech_pcm

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content_stream.return_value = iter(
            [_stream_chunk(b"\x01\x00"), _stream_chunk(b"\x02\x00\x03\x00")]
        )
        chunks = list(stream_speech_pcm("【対応指示】\nAP を確認してください。", agent_id="network_specialist"))

    assert chunks == [b"\x01\x00", b"\x02\x00\x03\x00"]
    kwargs = mock_client_cls.return_value.models.generate_content_stream.call_args.kwargs
    assert kwargs["config"].speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"
    assert tts_module._PREFERRED_TTS_MODEL == kwargs["model"]


def test_stream_speech_pcm_falls_through_failed_model():
    from app.tts import stream_speech_pcm

    calls: list[str] = []

    def fake_stream(model, contents, config):
        calls.append(model)
        if model != tts_module.TTS_MODEL_CHAIN[-1]:
            raise RuntimeError("404 model not found")
        return iter([_stream_chunk(b"\x00\x00")])

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content_stream.side_effect = fake_stream
        assert list(stream_speech_pcm("テストです。", agent_id="pos_specialist")) == [b"\x00\x00"]
    assert calls == tts_module.TTS_MODEL_CHAIN


def test_stream_speech_pcm_raises_when_no_audio():
    from app.tts import stream_speech_pcm

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content_stream.side_effect = RuntimeError("down")
        with pytest.raises(RuntimeError):
            list(stream_speech_pcm("テストです。"))


def test_tts_stream_endpoint_streams_pcm_and_passes_agent_id():
    from fastapi.testclient import TestClient

    from app.fast_api_app import app

    with patch("app.tts.stream_speech_pcm", return_value=iter([b"\x01\x00", b"\x02\x00"])) as mock_stream:
        res = TestClient(app).post("/api/tts/stream", json={"text": "テスト", "agent_id": "infra_specialist"})
    assert res.status_code == 200
    assert res.content == b"\x01\x00\x02\x00"
    assert res.headers["x-sample-rate"] == "24000"
    mock_stream.assert_called_once_with("テスト", agent_id="infra_specialist")


def test_tts_stream_endpoint_returns_502_without_audio():
    from fastapi.testclient import TestClient

    from app.fast_api_app import app

    def failing(*_args, **_kwargs):
        raise RuntimeError("no audio")
        yield b""  # pragma: no cover

    with patch("app.tts.stream_speech_pcm", side_effect=failing):
        res = TestClient(app).post("/api/tts/stream", json={"text": "テスト"})
    assert res.status_code == 502
    assert res.json()["status"] == "error"
