"""ADK 2.x Gemini Live API (gemini-3.8-live) WebSocket Bridge.

公式ナレッジ (adk.dev/live/custom-server) に基づく双方向音声ストリーミングブリッジ:
- Runner.run_live() + LiveRequestQueue による非同期ストリーミング
- 入力: 16kHz PCM (audio/pcm;rate=16000) または テキスト
- 出力: 24kHz PCM (audio/pcm;rate=24000) + 入出力トランスクリプション
- gemini-3.8-live 最新仕様:
    - response_modalities=["AUDIO"]
    - input_audio_transcription & output_audio_transcription 有効
    - thinking_config / thinking_level は非対応のため指定しない
    - proactive_audio: false や enable_affective_dialog は指定しない
- オフライン / APIキー未設定環境用のモック音声フォールバック内蔵
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types

from app.agent import (
    LIVE_API_LOCATION,
    LIVE_FALLBACK_MODEL_NAME,
    LIVE_MODEL_NAME,
    build_live_app,
    run_aiops_pipeline,
)
from app.tts import clean_markdown_for_speech, extract_natural_speech_script

logger = logging.getLogger("aiops.live_bridge")

_SPEAK_INSTRUCTION = (
    "あなたは読み上げ専用のナレーターです。次の【原稿】を一字一句そのまま、"
    "落ち着いた自然な日本語で読み上げてください。"
    "要約・言い換え・前置き・補足・質問はせず、ツールも使わないでください。"
)


def build_speak_prompt(answer_text: str) -> str | None:
    """専門AIの回答から Live 読み上げ依頼用プロンプトを組み立てる. 読み上げ対象が無ければ None."""
    if not (answer_text or "").strip():
        return None
    speech_text, _ = extract_natural_speech_script(answer_text)
    if not speech_text.strip():
        speech_text = clean_markdown_for_speech(answer_text)
    if not speech_text.strip("、。 \n"):
        return None
    return f"{_SPEAK_INSTRUCTION}\n\n【原稿】\n{speech_text.strip()}"


async def handle_live_websocket(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
    runner: Runner | None = None,
) -> None:
    """FastAPI WebSocket エンドポイント (/ws/live/{user_id}/{session_id}) の接続処理."""
    await websocket.accept()
    logger.info(
        f"WebSocket client connected: user={user_id}, session={session_id}, model={LIVE_MODEL_NAME}"
    )

    # クライアントへ接続完了イベントを送信（実際の Live モデル名を通知）
    await websocket.send_json(
        {
            "type": "connected",
            "user_id": user_id,
            "session_id": session_id,
            "live_model": LIVE_MODEL_NAME,
            "sample_rate_in": 16000,
            "sample_rate_out": 24000,
        }
    )

    # Gemini APIキーまたはVertex AI認証の確認
    has_api_credentials = bool(
        os.getenv("GEMINI_API_KEY")
        or (os.getenv("GOOGLE_GENAI_USE_VERTEXAI") and os.getenv("GOOGLE_CLOUD_PROJECT"))
    )

    if not has_api_credentials or runner is None:
        # オフライン / モックフォールバックモードで対話
        await _handle_mock_live_loop(websocket, user_id, session_id)
        return

    # 本番 Live API 接続モード
    # Live 用リージョンは app.agent.build_live_app 内で LIVE_API_LOCATION を明示指定している
    # (テキスト用モデルの GOOGLE_CLOUD_LOCATION は変更しない)
    try:
        run_config = RunConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        # 上り/下りタスクで共有するキュー (モデル切替時に差し替える)
        queue_holder: dict[str, LiveRequestQueue] = {"q": LiveRequestQueue()}

        async def upstream_task() -> None:
            """WebSocket からメッセージを受信し LiveRequestQueue へ転送."""
            try:
                while True:
                    raw_data = await websocket.receive_text()
                    msg = json.loads(raw_data)
                    msg_type = msg.get("type", "text")
                    queue = queue_holder["q"]

                    if msg_type == "text":
                        text = msg.get("text", "")
                        content = types.Content(
                            role="user", parts=[types.Part.from_text(text=text)]
                        )
                        queue.send_content(content)

                    elif msg_type == "audio_pcm":
                        # 16kHz 16-bit Mono PCM (Base64)
                        b64_audio = msg.get("data", "")
                        pcm_bytes = base64.b64decode(b64_audio)
                        queue.send_realtime(
                            types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
                        )

                    elif msg_type == "activity_end":
                        queue.send_activity_end()

                    elif msg_type == "speak":
                        # 専門AIの回答を Live のネイティブ音声でそのまま読み上げる
                        prompt = build_speak_prompt(msg.get("text", ""))
                        if prompt:
                            queue.send_content(
                                types.Content(
                                    role="user", parts=[types.Part.from_text(text=prompt)]
                                )
                            )
                        else:
                            await websocket.send_json({"type": "turn_complete"})

            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.error(f"Live upstream task error: {e}", exc_info=True)

        async def _stream_with_model(model_name: str) -> bool:
            """指定モデルで run_live を実行する. イベントを1件以上受信できたら True."""
            live_runner = Runner(
                app=build_live_app(model_name),
                session_service=runner.session_service,
                artifact_service=runner.artifact_service,
                auto_create_session=True,
            )
            received_any = False
            async for event in live_runner.run_live(
                user_id=user_id,
                session_id=session_id,
                live_request_queue=queue_holder["q"],
                run_config=run_config,
            ):
                received_any = True
                # トランスクリプション (リアルタイム文字起こし)
                if event.input_transcription and event.input_transcription.text:
                    await websocket.send_json(
                        {
                            "type": "transcription_input",
                            "text": event.input_transcription.text,
                            "finished": getattr(
                                event.input_transcription, "finished", False
                            ),
                        }
                    )

                if event.output_transcription and event.output_transcription.text:
                    await websocket.send_json(
                        {
                            "type": "transcription_output",
                            "author": event.author or "aiops_orchestrator",
                            "text": event.output_transcription.text,
                            "finished": getattr(
                                event.output_transcription, "finished", False
                            ),
                        }
                    )

                # 音声ストリーミングデータ (24kHz PCM)
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.inline_data and part.inline_data.mime_type.startswith(
                            "audio/"
                        ):
                            b64 = base64.b64encode(part.inline_data.data).decode("ascii")
                            await websocket.send_json(
                                {
                                    "type": "audio_chunk",
                                    "mime_type": part.inline_data.mime_type,
                                    "data": b64,
                                }
                            )

                # 割り込み / ターン完了 (ブラウザ側の再生制御用)
                if getattr(event, "interrupted", False):
                    await websocket.send_json({"type": "interrupted"})
                if getattr(event, "turn_complete", False):
                    await websocket.send_json({"type": "turn_complete"})
            return received_any

        async def downstream_task() -> None:
            """run_live() のイベントを受信し WebSocket クライアントへ転送 (モデル自動切替付き)."""
            candidates = [LIVE_MODEL_NAME]
            if LIVE_FALLBACK_MODEL_NAME and LIVE_FALLBACK_MODEL_NAME != LIVE_MODEL_NAME:
                candidates.append(LIVE_FALLBACK_MODEL_NAME)

            for idx, model_name in enumerate(candidates):
                try:
                    await _stream_with_model(model_name)
                    return
                except WebSocketDisconnect:
                    return
                except Exception as e:
                    has_next = idx + 1 < len(candidates)
                    logger.warning(
                        f"Live API streaming exception with {model_name} "
                        f"(location={LIVE_API_LOCATION}): {e}"
                    )
                    if has_next:
                        next_model = candidates[idx + 1]
                        queue_holder["q"] = LiveRequestQueue()
                        await websocket.send_json(
                            {"type": "model_switched", "live_model": next_model}
                        )
                        await websocket.send_json(
                            {"type": "connected", "live_model": next_model}
                        )
                        continue
                    await websocket.send_json(
                        {
                            "type": "warning",
                            "message": f"Live API unavailable ({e}), switched to local AIOps pipeline.",
                        }
                    )

        try:
            await asyncio.gather(upstream_task(), downstream_task())
        finally:
            queue_holder["q"].close()

    except Exception as e:
        logger.warning(f"Live bridge fallback to mock: {e}")
        await _handle_mock_live_loop(websocket, user_id, session_id)


async def _handle_mock_live_loop(
    websocket: WebSocket, user_id: str, session_id: str
) -> None:
    """APIキー未設定時またはオフライン環境用のフォールバック対話ループ."""
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            if data.get("type") == "speak":
                # モックでは音声生成しないため即完了を返し、クライアント側で TTS へフォールバック
                await websocket.send_json({"type": "turn_complete"})
                continue

            query = data.get("text", "")

            if not query and data.get("type") == "audio_pcm":
                query = "中央店のスマートカート専用ゲートで、専用ゲート通過認証エラー（ERR-GATE-408）が起きています。過去に似た事象はありますか？"

            if not query:
                continue

            # 入力トランスクリプション
            await websocket.send_json(
                {
                    "type": "transcription_input",
                    "text": query,
                    "finished": True,
                }
            )

            # 推論プロセスをリアルタイムにステップ順でストリーミング
            result = run_aiops_pipeline(query, session_id=session_id)
            for step in result["reasoning_steps"]:
                await asyncio.sleep(0.35)  # リアルタイム感の演出
                await websocket.send_json(
                    {
                        "type": "reasoning_step",
                        "step": step,
                    }
                )

            # 出力トランスクリプションと回答テキストをリアルタイム逐次ストリーミング
            answer_text = result["answer"]
            chunk_size = 8
            for i in range(0, len(answer_text), chunk_size):
                await asyncio.sleep(0.035)  # リアルタイムタイピング演出
                partial_text = answer_text[: i + chunk_size]
                is_last = (i + chunk_size) >= len(answer_text)
                await websocket.send_json(
                    {
                        "type": "transcription_output",
                        "author": result["assigned_agent"],
                        "author_label": result["assigned_agent_label"],
                        "text": partial_text,
                        "finished": is_last,
                        "full_result": result if is_last else None,
                    }
                )

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected gracefully: {session_id}")
    except Exception as e:
        logger.error(f"Mock live loop error: {e}")
