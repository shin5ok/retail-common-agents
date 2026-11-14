"""Gemini TTS & Google Cloud Neural2 音声合成モジュール.

SPEC.md 3.1 音声対話接点 & CE品質基準:
- Gemini TTS を公式仕様 (speech_config.voice_config.prebuilt_voice_config) で呼び出し、自然なトーンで音声生成
- 提供リージョンを明示 (TTS_LOCATION, 既定 global。gemini-3.1-flash-tts-preview は global のみ提供)
- 生 PCM (audio/L16, 24kHz) を WAV 化してブラウザで確実に再生
- 多段フォールバック (TTS_MODEL_CHAIN -> ja-JP-Neural2-B) と成功モデルの記憶で遅延を抑制
- メモリキャッシュにより再訪時や連続再生時のレスポンスを短縮
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
import time
import wave
from collections.abc import Iterator
from typing import Any

import google.auth
import google.auth.transport.requests
import requests

logger = logging.getLogger("aiops.tts")

# メモリ内 TTS キャッシュ（最大100件）
_TTS_CACHE: dict[str, dict[str, Any]] = {}
_MAX_CACHE_SIZE = 100

# Gemini TTS 設定 (環境変数で上書き可能)
TTS_LOCATION = os.getenv("TTS_LOCATION", "global")
TTS_VOICE = os.getenv("TTS_VOICE", "Kore")
TTS_MODEL_CHAIN: list[str] = [
    m.strip()
    for m in os.getenv(
        "TTS_MODEL_CHAIN",
        "gemini-3.8-flash-lite-tts,gemini-3.8-flash-tts,gemini-3.1-flash-tts-preview",
    ).split(",")
    if m.strip()
]
_TTS_STYLE_PROMPT = "親切で落ち着いた現場オペレーターのトーンで、聞き取りやすく自然なイントネーションで次の文章を読み上げてください"

# エージェントごとの固定ボイス (アイコンの性別に合わせて割り当て)
#   gemini: Gemini TTS の prebuilt voice 名 / fallback: Cloud TTS 最終フォールバック音声名
DEFAULT_FALLBACK_VOICE = "ja-JP-Neural2-B"
AGENT_VOICES: dict[str, dict[str, str]] = {
    "aiops_orchestrator": {"gemini": "Kore", "fallback": "ja-JP-Neural2-B"},  # 女性
    "infra_specialist": {"gemini": "Charon", "fallback": "ja-JP-Neural2-D"},  # 男性
    "order_specialist": {"gemini": "Leda", "fallback": "ja-JP-Wavenet-A"},  # 女性
    "network_specialist": {"gemini": "Puck", "fallback": "ja-JP-Neural2-C"},  # 男性
    "pos_specialist": {"gemini": "Aoede", "fallback": "ja-JP-Wavenet-B"},  # 女性
}


def resolve_agent_voice(agent_id: str | None) -> tuple[str, str]:
    """エージェント ID から (Gemini TTS 声, フォールバック声) を返す. 未知/未指定は既定声."""
    voices = AGENT_VOICES.get(agent_id or "")
    if voices:
        return voices["gemini"], voices["fallback"]
    return TTS_VOICE, DEFAULT_FALLBACK_VOICE

_TTS_TIMEOUT_MS = int(os.getenv("TTS_TIMEOUT_MS", "15000"))

# 成功したモデルを記憶し、失敗したモデルは一定時間スキップ (遅延抑制)
_PREFERRED_TTS_MODEL: str | None = None
_FAILED_TTS_MODELS: dict[str, float] = {}
_FAILED_MODEL_TTL_SEC = 600

# 機器型番・エラーコード・専門用語の自然な日本語読み替え辞書
_PHONETIC_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    # 機器名・ノード名
    (re.compile(r"\bGATE-AP-02\b", re.IGNORECASE), "ゲート・エーピー・ゼロツー"),
    (re.compile(r"\bGATE-AP-01\b", re.IGNORECASE), "ゲート・エーピー・ゼロワン"),
    (re.compile(r"\bESL-GW-DELI-01\b", re.IGNORECASE), "イーエスエル・ゲートウェイ・デリ・ゼロワン"),
    (re.compile(r"\bESL-GW-[A-Z0-9-]+\b", re.IGNORECASE), "イーエスエル・ゲートウェイ"),
    (re.compile(r"\bPOS-REG-[0-9]+\b", re.IGNORECASE), "ピーオーエス・レジ"),
    (re.compile(r"\bPOS-01\b", re.IGNORECASE), "ピーオーエス・ゼロワン"),
    (re.compile(r"\bPOS-02\b", re.IGNORECASE), "ピーオーエス・ゼロツー"),
    (re.compile(r"\bORDERHUB\b", re.IGNORECASE), "オーダーハブ"),
    # エラーコード
    (re.compile(r"\bERR-GATE-408\b", re.IGNORECASE), "エラー・ゲート・よんまるはち"),
    (re.compile(r"\bESL-SYNC-501\b", re.IGNORECASE), "エラー・イーエスエル・同期・ごーまるいち"),
    (re.compile(r"\bERR-CART-WGT-105\b", re.IGNORECASE), "エラー・カート・重量・いちまるご"),
    (re.compile(r"\bEDI-ERR-403\b", re.IGNORECASE), "イーディーアイ・エラー・よんまるさん"),
    (re.compile(r"\bERR-[A-Z0-9-]+\b", re.IGNORECASE), "システムエラー"),
    # ネットワーク・インフラ用語
    (re.compile(r"\bPoE\b", re.IGNORECASE), "ピーオーイー"),
    (re.compile(r"\bLAN\b", re.IGNORECASE), "ラン"),
    (re.compile(r"\bWAN\b", re.IGNORECASE), "ワン"),
    (re.compile(r"\bAP\b"), "エーピー"),
    (re.compile(r"\bSSID\b", re.IGNORECASE), "エスエスアイディー"),
    (re.compile(r"\bEDI\b", re.IGNORECASE), "イーディーアイ"),
    (re.compile(r"\bPOS\b", re.IGNORECASE), "ピーオーエス"),
    (re.compile(r"\bCPU\b", re.IGNORECASE), "シーピーユー"),
    (re.compile(r"\bRAM\b", re.IGNORECASE), "ラム"),
    (re.compile(r"\bOS\b", re.IGNORECASE), "オーエス"),
    (re.compile(r"\bDB\b", re.IGNORECASE), "ディービー"),
    (re.compile(r"\bAPI\b", re.IGNORECASE), "エーピーアイ"),
    (re.compile(r"\bIP\b", re.IGNORECASE), "アイピー"),
    (re.compile(r"\bDNS\b", re.IGNORECASE), "ディーエヌエス"),
    (re.compile(r"\bOKF\b", re.IGNORECASE), "オーケーエフ"),
    # チケット・数字
    (re.compile(r"#(\d+)", re.IGNORECASE), r"チケット番号 \1 "),
]


def clean_markdown_for_speech(text: str) -> str:
    """Markdown 記号、URL、構造化タグを音声読み上げ用に平易化する."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\|.*\|", "", text)
    text = re.sub(r"^[#\*\-]+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[\*_~]", "", text)
    text = re.sub(r"【(.*?)】", r"、\1についてです。、", text)
    text = re.sub(r"\n+", "、", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_natural_speech_script(raw_text: str) -> tuple[str, str]:
    """回答文から耳で聞いて最も理解しやすいアナウンス原稿と SSML を高速生成する."""
    if not raw_text.strip():
        return "", ""

    instruction_part = ""
    reason_part = ""

    inst_match = re.search(r"【対応指示】([\s\S]*?)(?=【|$)", raw_text)
    if inst_match:
        instruction_part = inst_match.group(1).strip()

    reason_match = re.search(r"【(?:原因・理由|理由|原因)】([\s\S]*?)(?=【|$)", raw_text)
    if reason_match:
        reason_part = reason_match.group(1).strip()

    sentences: list[str] = []

    if instruction_part:
        lines = [line.strip() for line in instruction_part.splitlines() if line.strip()]
        clean_lines = []
        for line in lines[:3]:
            cl = re.sub(r"^[0-9]+[\.\)]\s*", "", line)
            cl = re.sub(r"^[-*]\s*", "", cl)
            cl = clean_markdown_for_speech(cl)
            if cl:
                clean_lines.append(cl)

        if clean_lines:
            sentences.append("対応手順をお伝えします。")
            if len(clean_lines) == 1:
                sentences.append(f"{clean_lines[0].rstrip('。、')}。")
            else:
                for idx, cl in enumerate(clean_lines):
                    prefix = "まず、" if idx == 0 else "続いて、"
                    sentences.append(f"{prefix}{cl.rstrip('。、')}。")

        if reason_part:
            clean_reason = clean_markdown_for_speech(
                reason_part.splitlines()[0] if reason_part.splitlines() else reason_part
            ).rstrip("。、")
            if clean_reason:
                sentences.append(f"原因は、{clean_reason}です。")

        sentences.append("詳しい手順や画面ログは、端末画面をご確認ください。")
    else:
        cleaned = clean_markdown_for_speech(raw_text)
        raw_sentences = [s.strip().rstrip("。、") for s in re.split(r"[。！？\n]", cleaned) if s.strip()]
        selected = raw_sentences[:3]
        if selected:
            for s in selected:
                sentences.append(f"{s}。")
        else:
            sentences.append(cleaned[:150].rstrip("。、") + "。")

    processed_sentences: list[str] = []
    for s in sentences:
        curr = s
        for pattern, replacement in _PHONETIC_REPLACEMENTS:
            curr = pattern.sub(replacement, curr)
        curr = re.sub(r"、+", "、", curr)
        curr = re.sub(r"。+", "。", curr)
        curr = re.sub(r"、。", "。", curr)
        curr = re.sub(r"^[、\s]+", "", curr)
        processed_sentences.append(curr.strip())

    plain_text = "".join(processed_sentences)

    ssml_parts = ["<speak>"]
    for s in processed_sentences:
        if s:
            ssml_parts.append(f"<s>{s}</s><break time=\"250ms\"/>")
    ssml_parts.append("</speak>")
    ssml_text = "".join(ssml_parts)

    return plain_text, ssml_text


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Gemini TTS が返す生 PCM (s16le) をブラウザで再生可能な WAV に変換する."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _parse_sample_rate(mime_type: str, default: int = 24000) -> int:
    """`audio/L16;codec=pcm;rate=24000` 形式の MIME からサンプルレートを取得する."""
    match = re.search(r"rate=(\d+)", mime_type or "")
    return int(match.group(1)) if match else default


def _to_playable_audio(audio_bytes: bytes, mime_type: str) -> tuple[str, str]:
    """音声バイト列を (base64, mime_type) に変換する. 生 PCM は WAV 化する."""
    mime = (mime_type or "").lower()
    if "l16" in mime or "pcm" in mime or not mime:
        wav = pcm_to_wav(audio_bytes, sample_rate=_parse_sample_rate(mime))
        return base64.b64encode(wav).decode("utf-8"), "audio/wav"
    return base64.b64encode(audio_bytes).decode("utf-8"), mime_type


def _cloud_tts_endpoint(location: str) -> str:
    """Cloud Text-to-Speech API のリージョン別エンドポイント (global は接頭辞なし)."""
    if not location or location == "global":
        return "https://texttospeech.googleapis.com/v1/text:synthesize"
    return f"https://{location}-texttospeech.googleapis.com/v1/text:synthesize"


def _synthesize_with_gemini_tts(
    plain_text: str,
    model_name: str = "gemini-3.8-flash-lite-tts",
    voice_name: str | None = None,
) -> dict[str, Any] | None:
    """Gemini TTS モデルで音声を合成する (GenAI SDK → Cloud TTS API の順に試行).

    Vertex AI 利用時は TTS 提供リージョン (TTS_LOCATION, 既定 global) を明示し、
    テキスト/Live 用の GOOGLE_CLOUD_LOCATION の影響を受けないようにする。
    """
    voice_name = voice_name or TTS_VOICE

    # 1. Google GenAI SDK による直接音声生成
    try:
        from google import genai
        from google.genai import types

        http_options = types.HttpOptions(timeout=_TTS_TIMEOUT_MS)
        if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1"):
            client = genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=TTS_LOCATION,
                http_options=http_options,
            )
        else:
            client = genai.Client(http_options=http_options)

        response = client.models.generate_content(
            model=model_name,
            contents=f"{_TTS_STYLE_PROMPT}: {plain_text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    language_code="ja-JP",
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                    ),
                ),
            ),
        )
        if response and response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        raw = part.inline_data.data
                        if isinstance(raw, str):
                            raw = base64.b64decode(raw)
                        b64_audio, mime = _to_playable_audio(raw, part.inline_data.mime_type or "")
                        return {
                            "status": "success",
                            "audio_content": b64_audio,
                            "mime_type": mime,
                            "speech_text": plain_text,
                            "voice": f"{model_name} (Gemini TTS / {voice_name} / {TTS_LOCATION})",
                        }
    except Exception as e:
        logger.info(f"GenAI SDK TTS with {model_name} ({TTS_LOCATION}) failed: {e}")

    # 2. Cloud Text-to-Speech API (Gemini-TTS モデル指定)
    try:
        creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)

        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
            "x-goog-user-project": project,
        }
        payload = {
            "input": {"prompt": _TTS_STYLE_PROMPT, "text": plain_text},
            "voice": {"languageCode": "ja-JP", "name": voice_name, "modelName": model_name},
            "audioConfig": {"audioEncoding": "MP3"},
        }

        resp = requests.post(_cloud_tts_endpoint(TTS_LOCATION), headers=headers, json=payload, timeout=8)
        if resp.status_code == 200:
            audio_content = resp.json().get("audioContent", "")
            if audio_content:
                return {
                    "status": "success",
                    "audio_content": audio_content,
                    "mime_type": "audio/mp3",
                    "speech_text": plain_text,
                    "voice": f"{model_name} (Cloud TTS Gemini-TTS / {voice_name} / {TTS_LOCATION})",
                }
        else:
            logger.info(f"Cloud TTS API with model {model_name} returned {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        logger.info(f"Cloud TTS API Gemini model call exception: {e}")

    return None


def _store_cache(cache_key: str, result: dict[str, Any]) -> None:
    if len(_TTS_CACHE) >= _MAX_CACHE_SIZE:
        _TTS_CACHE.pop(next(iter(_TTS_CACHE)))
    _TTS_CACHE[cache_key] = result


STREAM_SAMPLE_RATE = 24000


def stream_speech_pcm(raw_text: str, agent_id: str | None = None) -> Iterator[bytes]:
    """Gemini TTS のストリーミング生成で、合成済みの PCM (s16le, 24kHz, mono) を逐次返す.

    全文の合成完了を待たずに先頭チャンクから再生できるため、初回発声までの遅延を大幅に短縮する。
    エージェント固有の声 (AGENT_VOICES) を使用する。音声が 1 チャンクも得られない場合は例外を送出し、
    呼び出し側で /api/tts (Neural2 フォールバック付き) に切り替える。
    """
    global _PREFERRED_TTS_MODEL
    if not raw_text.strip():
        raise ValueError("Text is empty")

    from google import genai
    from google.genai import types

    gemini_voice, _ = resolve_agent_voice(agent_id)
    plain_speech_text, _ = extract_natural_speech_script(raw_text)

    chain = list(TTS_MODEL_CHAIN)
    if _PREFERRED_TTS_MODEL in chain:
        chain.remove(_PREFERRED_TTS_MODEL)
        chain.insert(0, _PREFERRED_TTS_MODEL)
    now = time.monotonic()
    candidates = [
        m for m in chain if not (m in _FAILED_TTS_MODELS and now - _FAILED_TTS_MODELS[m] < _FAILED_MODEL_TTL_SEC)
    ]

    http_options = types.HttpOptions(timeout=_TTS_TIMEOUT_MS)
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1"):
        client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=TTS_LOCATION,
            http_options=http_options,
        )
    else:
        client = genai.Client(http_options=http_options)

    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            language_code="ja-JP",
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=gemini_voice)
            ),
        ),
    )

    last_error: Exception | None = None
    for model_name in candidates:
        yielded = False
        try:
            stream = client.models.generate_content_stream(
                model=model_name,
                contents=f"{_TTS_STYLE_PROMPT}: {plain_speech_text}",
                config=config,
            )
            for chunk in stream:
                for candidate in chunk.candidates or []:
                    if not candidate.content or not candidate.content.parts:
                        continue
                    for part in candidate.content.parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and inline.data:
                            raw = inline.data
                            if isinstance(raw, str):
                                raw = base64.b64decode(raw)
                            if not yielded:
                                _PREFERRED_TTS_MODEL = model_name
                                _FAILED_TTS_MODELS.pop(model_name, None)
                                yielded = True
                            yield raw
        except Exception as e:
            if yielded:
                # 再生途中の失敗は後続モデルで最初から読み直すと重複するため打ち切る
                logger.warning(f"Streaming TTS interrupted ({model_name}): {e}")
                return
            logger.info(f"Streaming TTS with {model_name} ({TTS_LOCATION}) failed: {e}")
            last_error = e
        if yielded:
            return
        _FAILED_TTS_MODELS[model_name] = time.monotonic()

    raise RuntimeError(f"Streaming TTS produced no audio: {last_error}")


def synthesize_speech(raw_text: str, agent_id: str | None = None) -> dict[str, Any]:
    """Gemini TTS を最優先に、高品質で人間らしい日本語音声を合成する.

    agent_id を指定すると AGENT_VOICES に定義されたエージェント固有の声で読み上げる
    (未指定・未知の場合は TTS_VOICE と ja-JP-Neural2-B)。

    フォールバックチェーン (TTS_MODEL_CHAIN で変更可):
    1. gemini-3.8-flash-lite-tts (低遅延・リアルタイム音声エージェント向け)
    2. gemini-3.8-flash-tts (スタジオ品質)
    3. gemini-3.1-flash-tts-preview (global リージョンで提供確認済み)
    4. ja-JP-Neural2-B (Google Cloud Neural2)

    一度成功したモデルを記憶して次回から最優先で使い、失敗したモデルは以後スキップして遅延を抑える。
    """
    global _PREFERRED_TTS_MODEL
    if not raw_text.strip():
        return {"status": "error", "message": "Text is empty"}

    gemini_voice, fallback_voice = resolve_agent_voice(agent_id)
    cache_key = hashlib.md5(f"{gemini_voice}|{raw_text.strip()}".encode("utf-8")).hexdigest()
    if cache_key in _TTS_CACHE:
        logger.info("TTS cache hit for query")
        return _TTS_CACHE[cache_key]

    plain_speech_text, ssml_text = extract_natural_speech_script(raw_text)

    chain = list(TTS_MODEL_CHAIN)
    if _PREFERRED_TTS_MODEL in chain:
        chain.remove(_PREFERRED_TTS_MODEL)
        chain.insert(0, _PREFERRED_TTS_MODEL)

    now = time.monotonic()
    for model_name in chain:
        failed_at = _FAILED_TTS_MODELS.get(model_name)
        if failed_at is not None and now - failed_at < _FAILED_MODEL_TTL_SEC:
            continue
        result = _synthesize_with_gemini_tts(plain_speech_text, model_name=model_name, voice_name=gemini_voice)
        if result:
            _PREFERRED_TTS_MODEL = model_name
            _FAILED_TTS_MODELS.pop(model_name, None)
            _store_cache(cache_key, result)
            return result
        _FAILED_TTS_MODELS[model_name] = time.monotonic()

    # 最終フォールバック: Google Cloud Neural2 (SSML ポーズ制御・自然ピッチ)
    try:
        creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)

        url = "https://texttospeech.googleapis.com/v1/text:synthesize"
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
            "x-goog-user-project": project,
        }
        payload = {
            "input": {"ssml": ssml_text} if ssml_text else {"text": plain_speech_text},
            "voice": {
                "languageCode": "ja-JP",
                "name": fallback_voice,
            },
            "audioConfig": {
                "audioEncoding": "MP3",
                "speakingRate": 1.03,
                "pitch": 0.0,
            },
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            audio_content = data.get("audioContent", "")
            result = {
                "status": "success",
                "audio_content": audio_content,
                "mime_type": "audio/mp3",
                "speech_text": plain_speech_text,
                "voice": f"{fallback_voice} (Google Cloud TTS Fallback)",
            }
            if len(_TTS_CACHE) >= _MAX_CACHE_SIZE:
                _TTS_CACHE.pop(next(iter(_TTS_CACHE)))
            _TTS_CACHE[cache_key] = result
            return result
        else:
            logger.warning(f"Google Cloud TTS API error: {resp.status_code} {resp.text}")
            return {
                "status": "fallback",
                "speech_text": plain_speech_text,
                "message": f"TTS API error ({resp.status_code})",
            }
    except Exception as e:
        logger.warning(f"TTS synthesis exception: {e}")
        return {
            "status": "fallback",
            "speech_text": plain_speech_text,
            "message": str(e),
        }
