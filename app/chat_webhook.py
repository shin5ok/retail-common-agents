"""Google Chat 連携 Webhook ハンドラー (Layer 1).

SPEC.md 3.1 ユーザー接点機能:
Google Chat スペースでのメンションまたは直接メッセージをトリガーとして
問い合わせを受け付け、Cards v2 形式およびテキストでリッチに返信します。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.agent import run_aiops_pipeline

logger = logging.getLogger("aiops.chat_webhook")
chat_router = APIRouter()


@chat_router.post("/api/webhook/google-chat")
async def handle_google_chat_webhook(request: Request) -> JSONResponse:
    """Google Chat からの Webhook イベントを受信・処理するエンドポイント."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    event_type = body.get("type", "MESSAGE")
    message = body.get("message", {})
    user_text = message.get("argumentText") or message.get("text") or ""
    user_text = user_text.strip()

    # Bot追加時のウェルカムメッセージ
    if event_type == "ADDED_TO_SPACE":
        return JSONResponse(
            {
                "text": (
                    "👋 AIOps マルチエージェント運用基盤へようこそ！\n"
                    "店舗設備障害やスマートストアのトラブルに関する問い合わせを自然言語で話しかけてください。\n"
                    "例: 『中央店のスマートカート専用ゲートで通過認証エラー（ERR-GATE-408）が起きています。過去に似た事象はありますか？』"
                )
            }
        )

    # 問い合わせの実行
    query = (
        user_text
        if user_text
        else "中央店のスマートカート専用ゲートで、専用ゲート通過認証エラー（ERR-GATE-408）が起きています。過去に似た事象はありますか？"
    )
    result = run_aiops_pipeline(query)

    top_ticket = result["tickets"][0] if result["tickets"] else None
    runbook = result["runbook"]

    # Google Chat Cards v2 形式のレスポンス
    card_sections: list[dict[str, Any]] = [
        {
            "header": "🤖 AIOps 一次切り分け結果",
            "widgets": [
                {
                    "decoratedText": {
                        "topLabel": "担当スペシャリスト",
                        "text": f"<b>{result['assigned_agent_label']}</b>",
                        "startIcon": {"knownIcon": "MEMBERSHIP"},
                    }
                },
                {"textParagraph": {"text": result["answer"].replace("\n", "<br/>")}},
            ],
        }
    ]

    if top_ticket:
        card_sections.append(
            {
                "header": f"📋 過去の類似インシデント照合 ({top_ticket['ticket_id']})",
                "widgets": [
                    {
                        "decoratedText": {
                            "topLabel": "チケット番号・件名",
                            "text": f"<b>{top_ticket['ticket_id']}: {top_ticket['title']}</b>",
                            "bottomLabel": f"類似度スコア: {top_ticket.get('similarity_score', 0.95) * 100:.0f}%",
                        }
                    },
                    {
                        "decoratedText": {
                            "topLabel": "過去の根本原因",
                            "text": top_ticket["root_cause"],
                            "wrapText": True,
                        }
                    },
                ],
            }
        )

    card_sections.append(
        {
            "header": "🚨 エスカレーション窓口",
            "widgets": [
                {
                    "decoratedText": {
                        "topLabel": "有人サポートデスク",
                        "text": f"<b>{result['escalation_team']}</b>",
                        "bottomLabel": "一次切り分け手順で解決しない場合は内線またはオンコールへ",
                    }
                }
            ],
        }
    )

    response_payload = {
        "cardsV2": [
            {
                "cardId": "aiops-incident-card",
                "card": {
                    "header": {
                        "title": "AIOps 運用自動切り分け支援",
                        "subtitle": f"対象ドメイン: {result['domain'].upper()}",
                        "imageUrl": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/smart_toy/default/48px.svg",
                        "imageType": "CIRCLE",
                    },
                    "sections": card_sections,
                },
            }
        ]
    }

    return JSONResponse(response_payload)
