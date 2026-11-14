"""FastAPI メインサーバー (Layer 1, 2, 3 統合).

ローカル環境および Cloud Run (PORT 8080) で単一軽量プロセスとして稼働:
- ADK 2.x API (/run_sse, /apps/..., /list-apps)
- A2A プロトコル (/a2a/app)
- AIOps カスタム API (/api/chat, /api/scenarios, /api/knowledge)
- Gemini Live API WebSocket (/ws/live/{user_id}/{session_id})
- Google Chat Webhook (/api/webhook/google-chat)
- Web ポータル UI (React/Tailwind 静的アセット配信)
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from pydantic import BaseModel
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.chat_webhook import chat_router
from app.live_bridge import handle_live_websocket
from app.rag_store import (
    DEMO_SCENARIOS,
    MOCK_RUNBOOKS,
    MOCK_SPLUNK_LOGS,
    MOCK_TICKETS,
)

load_dotenv()

allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",")
    if os.getenv("ALLOW_ORIGINS")
    else ["*"]
)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIST = Path(AGENT_DIR) / "frontend" / "dist"


class ChatRequest(BaseModel):
    query: str
    session_id: str = "default-session"
    image_data: str | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name

    try:
        await attach_a2a_routes(
            app,
            agent=root_agent,
            runner=runner,
            task_store=InMemoryTaskStore(),
            rpc_path=f"/a2a/{adk_app.name}",
        )
    except Exception as e:
        # A2A route attachment logging
        pass

    yield


# ADK 2.x の get_fast_api_app
app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=False,  # カスタム AIOps ポータル UI をルート配信するため False
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=False,
    lifespan=lifespan,
)

app.title = "AIOps Multi-Agent Platform"
app.description = "AIOps マルチエージェント自動一次切り分けデモ基盤"

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# IAP (Identity-Aware Proxy) 認可ミドルウェア
# =====================================================================
@app.middleware("http")
async def iap_authorization_middleware(request: Request, call_next):
    """Google Cloud Run Native IAP 認可ミドルウェア.

    環境変数 IAP_USERS が指定されている場合、リクエストヘッダー
    X-Goog-Authenticated-User-Email を検証し、ホワイトリスト外のアクセスを 403 で拒絶します。
    未設定時（ローカル開発など）や除外パス（/health, CORS OPTIONS等）はバイパスします。
    """
    # プリフライト(OPTIONS)、ヘルスチェック、API docs、Google Chat Webhook 等はバイパス
    bypass_paths = {"/health", "/docs", "/openapi.json"}
    if (
        request.method == "OPTIONS"
        or request.url.path in bypass_paths
        or request.url.path.startswith("/api/chat/webhook")
        or request.url.path.startswith("/api/webhook")
    ):
        return await call_next(request)

    # Cloud Run 環境 (K_SERVICE 設定時) または明示的なテスト環境 (ENABLE_IAP_CHECK) でのみ検証を強制
    is_cloud_run = bool(os.getenv("K_SERVICE"))
    force_iap_check = os.getenv("ENABLE_IAP_CHECK", "").lower() in ("true", "1")
    if not (is_cloud_run or force_iap_check):
        return await call_next(request)

    iap_users_raw = os.getenv("IAP_USERS", "").strip()
    if not iap_users_raw:
        # IAP_USERS 未設定時はチェックをスキップ (ローカル開発環境等の後方互換性維持)
        return await call_next(request)

    allowed_users = {u.strip().lower() for u in iap_users_raw.split(",") if u.strip()}

    # IAP ヘッダーの確認: accounts.google.com:user@example.com
    user_email_header = request.headers.get("X-Goog-Authenticated-User-Email", "")
    user_email = ""
    if user_email_header:
        if ":" in user_email_header:
            user_email = user_email_header.split(":", 1)[1].strip().lower()
        else:
            user_email = user_email_header.strip().lower()

    if not user_email or user_email not in allowed_users:
        return JSONResponse(
            status_code=403,
            content={
                "error": "Forbidden",
                "message": f"Access denied for user '{user_email or 'anonymous'}'. Not authorized in IAP_USERS.",
            },
        )

    # 認可済みユーザーのメールアドレスをリクエスト状態に保存
    request.state.user_email = user_email
    return await call_next(request)


# Google Chat Webhook Router
app.include_router(chat_router)


# =====================================================================
# REST API エンドポイント
# =====================================================================


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Cloud Run 用ヘルスチェックエンドポイント."""
    return {"status": "ok", "app": "aiops-multi-agent", "version": "1.0.0"}


@app.get("/api/scenarios")
async def get_scenarios() -> list[dict[str, Any]]:
    """ワンクリックデモ用プリセットシナリオ一覧を取得 (OKF動的ロード対応)."""
    try:
        from app.okf_manager import get_okf_manager

        scenarios = get_okf_manager().get_scenarios()
        if scenarios:
            return scenarios
    except Exception:
        pass
    return DEMO_SCENARIOS


@app.get("/api/knowledge")
async def get_knowledge_base() -> dict[str, Any]:
    """デモ用の全ナレッジベース（チケット、Splunkログ、Runbook）を取得."""
    return {
        "tickets": [t.to_dict() for t in MOCK_TICKETS],
        "logs": [log.to_dict() for log in MOCK_SPLUNK_LOGS],
        "runbooks": [rb.to_dict() for rb in MOCK_RUNBOOKS],
    }


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest) -> JSONResponse:
    """AIOps 問い合わせエンドポイント (意図判定・ルーティング・RAG・推論可視化データ返却)."""
    from app.agent import run_aiops_pipeline

    result = run_aiops_pipeline(query=req.query, session_id=req.session_id, image_data=req.image_data)
    return JSONResponse(result)


class TTSRequest(BaseModel):
    text: str
    agent_id: str | None = None


@app.post("/api/tts")
async def tts_endpoint(req: TTSRequest) -> JSONResponse:
    """Gemini Flash × Google Cloud Neural2 TTS 音声合成エンドポイント (agent_id でエージェント固有の声)."""
    from app.tts import synthesize_speech

    result = synthesize_speech(req.text, agent_id=req.agent_id)
    return JSONResponse(result)


@app.post("/api/tts/stream", response_model=None)
async def tts_stream_endpoint(req: TTSRequest) -> StreamingResponse | JSONResponse:
    """Gemini TTS ストリーミング音声 (生 PCM s16le / 24kHz / mono) を逐次返す低遅延エンドポイント.

    先頭チャンクが得られない場合は 502 を返し、フロントは /api/tts にフォールバックする。
    """
    from app.tts import STREAM_SAMPLE_RATE, stream_speech_pcm

    gen = stream_speech_pcm(req.text, agent_id=req.agent_id)
    try:
        first = await run_in_threadpool(next, gen, None)
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"stream tts failed: {e}"}, status_code=502)
    if not first:
        return JSONResponse({"status": "error", "message": "stream tts produced no audio"}, status_code=502)

    async def body() -> AsyncIterator[bytes]:
        yield first
        async for chunk in iterate_in_threadpool(gen):
            yield chunk

    return StreamingResponse(
        body(),
        media_type=f"audio/L16; rate={STREAM_SAMPLE_RATE}; channels=1",
        headers={"X-Sample-Rate": str(STREAM_SAMPLE_RATE), "Cache-Control": "no-store"},
    )


class MatchFollowupRequest(BaseModel):
    voice_text: str
    candidates: list[str] = []


@app.post("/api/match-followup")
async def match_followup_endpoint(req: MatchFollowupRequest) -> JSONResponse:
    """音声入力テキストと続けて質問の候補群を自然言語意味照合."""
    from app.followup_matcher import match_followup_question

    result = match_followup_question(
        voice_text=req.voice_text,
        candidates=req.candidates,
    )
    return JSONResponse(result)


# =====================================================================
# WebSocket エンドポイント (Gemini Live API)
# =====================================================================


@app.websocket("/ws/live/{user_id}/{session_id}")
async def live_websocket_endpoint(
    websocket: WebSocket, user_id: str, session_id: str
) -> None:
    """双方向音声・テキストストリーミング用 WebSocket エンドポイント."""
    runner = getattr(app.state, "runner", None)
    await handle_live_websocket(
        websocket=websocket,
        user_id=user_id,
        session_id=session_id,
        runner=runner,
    )


# =====================================================================
# 静的フロントエンド配信 (SPA Routing)
# =====================================================================
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="assets",
        )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/")
    async def serve_fallback_portal():
        return HTMLResponse(
            """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>AIOps Multi-Agent Platform</title>
  <style>
    body { font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; text-align: center; }
    h1 { color: #38bdf8; }
    .card { background: #1e293b; padding: 20px; border-radius: 8px; max-width: 600px; margin: 20px auto; }
    a { color: #38bdf8; text-decoration: none; }
  </style>
</head>
<body>
  <h1>AIOps マルチエージェント運用基盤 (Backend Ready)</h1>
  <div class="card">
    <p>FastAPI バックエンドおよび ADK 2.x が正常に起動しています。</p>
    <p><a href="/docs">API ドキュメント (Swagger UI) を開く</a></p>
    <p><a href="/api/scenarios">デモシナリオ一覧を確認する</a></p>
  </div>
</body>
</html>"""
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app.fast_api_app:app", host="0.0.0.0", port=port, reload=True)
