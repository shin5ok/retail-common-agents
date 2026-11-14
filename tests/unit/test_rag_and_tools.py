"""Unit tests for AIOps Multi-Agent Platform.

Verifies:
1. RAG Search Engine (tickets, Splunk logs, runbooks)
2. Golden Path Scenario (#12345 POS payment timeout & router reboot)
3. 4-domain routing accuracy (pos, infra, order, network)
4. FastAPI endpoints (/health, /api/scenarios, /api/chat)
5. Google Chat Webhook endpoint (/api/webhook/google-chat)
"""

import pytest
from fastapi.testclient import TestClient

from app.agent import run_aiops_pipeline
from app.fast_api_app import app
from app.rag_store import classify_domain, get_runbook, search_logs, search_tickets


def test_classify_domain_pos():
    """POS領域（電子棚札ESL）の意図判定をテスト."""
    query = "駅前店の惣菜売場で、電子棚札（ESL）に夕方のAI自動値下げ価格が反映されていません（ESL-SYNC-501）。復旧手順を教えてください。"
    result = classify_domain(query)
    assert result["domain"] == "pos"
    assert result["agent_id"] == "pos_specialist"
    assert result["confidence"] >= 0.8
    assert "電子棚札" in result["matched_terms"] or "esl" in result["matched_terms"] or "値下げ" in result["matched_terms"]


def test_classify_domain_all_specialists():
    """4専門領域の意図判定精度をテスト."""
    # 1. Infra
    res_infra = classify_domain("APサーバーのCPU使用率が100%に張り付いている")
    assert res_infra["domain"] == "infra"
    assert res_infra["agent_id"] == "infra_specialist"

    # 2. Order
    res_order = classify_domain("今日の生鮮品の発注データがシステムに反映されていない")
    assert res_order["domain"] == "order"
    assert res_order["agent_id"] == "order_specialist"

    # 3. Network
    res_net = classify_domain("西店のバックヤードでネットワークが繋がらずタブレットがオフライン")
    assert res_net["domain"] == "network"
    assert res_net["agent_id"] == "network_specialist"


def test_rag_search_pos_ticket():
    """POS領域でチケット #12110 が抽出されることをテスト."""
    query = "駅前店の惣菜売場で電子棚札の値段が変わりません。"
    tickets = search_tickets(query, domain="pos", top_k=2)
    assert len(tickets) > 0
    top = tickets[0]
    assert top["ticket_id"] == "#12110"
    assert "ESL-SYNC-501" in top["title"]
    assert "基地局" in top["resolution"] or "再起動" in top["resolution"]


def test_rag_search_splunk_logs():
    """Splunk エラーログ検索のテスト."""
    logs = search_logs("中央店 専用ゲート タイムアウト", domain="pos", top_k=2)
    assert len(logs) > 0
    assert any(log["error_code"] == "ERR-GATE-408" for log in logs)


def test_runbook_lookup():
    """4領域の標準復旧手順書（Runbook）取得テスト."""
    rb_pos = get_runbook("pos")
    assert rb_pos["runbook_id"] == "RB-POS-004"
    assert len(rb_pos["steps"]) >= 4

    rb_infra = get_runbook("infra")
    assert rb_infra["runbook_id"] == "RB-INF-001"


def test_run_aiops_pipeline_pos_esl():
    """E2E パイプライン（電子棚札値下げ未更新）の推論ステップと回答統合テスト."""
    query = "駅前店の惣菜売場で、電子棚札（ESL）に夕方のAI自動値下げ価格が反映されていません（ESL-SYNC-501）。復旧手順を教えてください。"
    result = run_aiops_pipeline(query)

    assert result["status"] == "success"
    assert result["domain"] == "pos"
    assert result["assigned_agent"] == "pos_specialist"
    assert "ESL" in result["answer"] or "基地局" in result["answer"] or "再起動" in result["answer"]
    assert "エスカレーション" in result["answer"]
    assert len(result["reasoning_steps"]) == 6
    assert result["reasoning_steps"][0]["type"] == "intent_classification"
    assert result["reasoning_steps"][1]["type"] == "agent_routing"
    assert result["reasoning_steps"][5]["type"] == "escalation_check"


def test_fastapi_rest_endpoints():
    """FastAPI REST API エンドポイントのテスト."""
    client = TestClient(app)

    # 1. Health check
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"

    # 2. Scenarios (4大スペシャリスト + 電子棚札)
    res_scenarios = client.get("/api/scenarios")
    assert res_scenarios.status_code == 200
    scenarios = res_scenarios.json()
    assert len(scenarios) == 5
    agent_ids = [s["agent_id"] for s in scenarios]
    assert agent_ids == [
        "infra_specialist",
        "order_specialist",
        "network_specialist",
        "pos_specialist",
        "pos_specialist",
    ]

    # 3. Knowledge base
    res_kb = client.get("/api/knowledge")
    assert res_kb.status_code == 200
    kb = res_kb.json()
    assert "tickets" in kb and len(kb["tickets"]) >= 4

    # 4. Chat endpoint
    res_chat = client.post(
        "/api/chat",
        json={
            "query": "駅前店の惣菜売場で、電子棚札にAI自動値下げ価格が反映されていません。",
            "session_id": "test-session",
        },
    )
    assert res_chat.status_code == 200
    chat_data = res_chat.json()
    assert chat_data["assigned_agent"] == "pos_specialist"
    assert "ESL" in chat_data["answer"] or "基地局" in chat_data["answer"] or "再起動" in chat_data["answer"]

    # 5. TTS endpoint
    res_tts = client.post(
        "/api/tts",
        json={"text": "テストメッセージです。"},
    )
    assert res_tts.status_code == 200
    tts_data = res_tts.json()
    assert tts_data["status"] in {"success", "fallback"}
    assert "speech_text" in tts_data


def test_google_chat_webhook():
    """Google Chat Webhook エンドポイントの Cards v2 レスポンステスト."""
    client = TestClient(app)

    # Message event
    res = client.post(
        "/api/webhook/google-chat",
        json={
            "type": "MESSAGE",
            "message": {
                "text": "中央店のスマートカート専用ゲートで通信エラーが発生しています。"
            },
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "cardsV2" in data
    cards = data["cardsV2"]
    assert len(cards) > 0
    header = cards[0]["card"]["header"]
    assert "AIOps" in header["title"]


def test_suggested_followups_and_followup_queries():
    """更問（フォローアップ質問）の候補生成と更問クエリへの文脈応答テスト."""
    # 1. 初期クエリで3件の更問候補が得られること
    init_res = run_aiops_pipeline(
        "駅前店の惣菜売場で電子棚札の値段が反映されません",
        session_id="session-followup-test",
    )
    assert "suggested_followups" in init_res
    assert len(init_res["suggested_followups"]) == 3
    assert any("変わりません" in q or "復旧" in q or "ランプ" in q for q in init_res["suggested_followups"])

    # 2. 更問「再起動しても値引き表示が変わりません」に対する2次回答
    followup_res = run_aiops_pipeline(
        "再起動しても値引き表示が変わりません",
        session_id="session-followup-test",
    )
    assert followup_res["status"] == "success"
    assert "更新されないことを確認しました" in followup_res["answer"]
    assert "緊急用値引シール" in followup_res["answer"] or "強制オフライン" in followup_res["answer"]

    # 3. 更問「ESL基地局の再起動後に確認するLEDランプ状態は？」に対する詳細手順回答
    detail_res = run_aiops_pipeline(
        "ESL基地局の再起動後に確認するLEDランプ状態は？",
        session_id="session-followup-test",
    )
    assert detail_res["status"] == "success"
    assert "LED" in detail_res["answer"] or "LAN" in detail_res["answer"] or "PWR" in detail_res["answer"]


def test_natural_language_contextual_followups():
    """自然言語の口語表現・文脈引き継ぎ・解決報告の対話テスト."""
    session_id = "test-session-natural-lang"

    # Step 1: 初回問い合わせ（電子棚札）
    res1 = run_aiops_pipeline("惣菜の電子棚札がタイムセール価格に切り替わらない", session_id=session_id)
    assert res1["domain"] == "pos"
    assert "#12110" in res1["tickets"][0]["ticket_id"]

    # Step 2: 自然言語での未復旧報告（機器名を言わずに「再起動したけど直らん」）
    res2 = run_aiops_pipeline("再起動したけど直らん", session_id=session_id)
    assert res2["domain"] == "pos"
    assert "再起動でも値引き表示が更新されないことを確認しました" in res2["answer"]
    assert "緊急用値引シール" in res2["answer"]

    # Step 3: 自然言語での詳細手順質問（「画面のどこ押せばいい？」）
    res3 = run_aiops_pipeline("画面のどこ押せばいい？", session_id=session_id)
    assert res3["domain"] == "pos"
    assert "手順" in res3["answer"] or "LED" in res3["answer"] or "ランプ" in res3["answer"]

    # Step 4: 自然言語での解決・感謝報告（「直った！ありがとう」）
    res4 = run_aiops_pipeline("直った！ありがとう", session_id=session_id)
    assert "無事にトラブルが復旧して安心いたしました" in res4["answer"]
    assert "正常完了" in res4["answer"]


def test_voice_and_natural_language_context_continuity():
    """マイク音声認識・口語表現・セッションID未指定時の文脈継続テスト."""
    # 1. カート重量検知トラブルを起点とする
    init_res = run_aiops_pipeline(
        "東店で、スマートカートのスキャン漏れ検知エラーが頻発してカートが止まります",
        session_id="voice-session-100",
    )
    assert init_res["domain"] == "pos"
    assert "重量センサー" in init_res["answer"] or "ゼロ点" in init_res["answer"]

    # 2. 音声入力で別セッションID（またはdefault）から「ゼロ点リセットを行ってもエラーが消えません」と発話
    voice_res1 = run_aiops_pipeline(
        "ゼロ点リセットを行ってもエラーが消えません",
        session_id="default",  # セッションIDが明示されないケース
    )
    assert voice_res1["domain"] == "pos"
    assert "予備カート" in voice_res1["answer"] or "利用を直ちに中止" in voice_res1["answer"] or "保守サポート" in voice_res1["answer"]

    # 3. 音声入力で「いまお客さん待たせてるから急ぎの代替方法教えて」と口頭相談
    voice_res2 = run_aiops_pipeline(
        "いまお客さん待たせてるから急ぎの代替方法教えて",
        session_id="voice-session-new",
    )
    assert voice_res2["domain"] == "pos"
    assert "予備カート" in voice_res2["answer"] or "乗換" in voice_res2["answer"]

    # 4. 音声入力で「レンズを拭くときの注意点を詳しく教えて」と詳細質問
    voice_res3 = run_aiops_pipeline(
        "レンズを拭くときの注意点を詳しく教えて",
        session_id="voice-session-new",
    )
    assert voice_res3["domain"] == "pos"
    assert "クロス" in voice_res3["answer"] or "アルコール" in voice_res3["answer"] or "カメラ" in voice_res3["answer"]

    # 5. 音声入力で「直りました、ありがとうございます」と感謝報告
    voice_res4 = run_aiops_pipeline(
        "直りました、ありがとうございます",
        session_id="voice-session-new",
    )
    assert "無事にトラブルが復旧して安心いたしました" in voice_res4["answer"]


def test_natural_language_routing_all_scenarios():
    """ワンクリックでお試しの全6ユースケースについて自然言語・現場表現での判別・ルーティングをテスト."""

    # 1. シナリオ: インフラ (AIカメラAPサーバーCPU100% #10892)
    infra_queries = [
        "サーバーが重くてタイムアウトが多発しているんですがどうすればいいですか？",
        "在庫管理APサーバーのCPUが100%に張り付いて504エラーが出てる",
    ]
    for q in infra_queries:
        res = run_aiops_pipeline(q, session_id=f"test-infra-{hash(q)}")
        assert res["status"] == "success"
        assert res["domain"] == "infra"
        assert res["assigned_agent"] == "infra_specialist"
        assert res["tickets"][0]["ticket_id"] == "#10892"
        assert "ローリング再起動" in res["answer"] or "再起動" in res["answer"]

    # 2. シナリオ: 電子棚札 (ESL AI値下げ未更新 #12110)
    esl_queries = [
        "惣菜売場の値札が変わらない",
        "夕方なのに電子棚札の値段が定価のままで半額や割引にならない",
    ]
    for q in esl_queries:
        res = run_aiops_pipeline(q, session_id=f"test-esl-{hash(q)}")
        assert res["status"] == "success"
        assert res["domain"] == "pos"
        assert res["assigned_agent"] == "pos_specialist"
        assert res["tickets"][0]["ticket_id"] == "#12110"
        assert "ESL" in res["answer"] or "電子棚札" in res["answer"]
        assert "基地局" in res["answer"] or "再起動" in res["answer"]

    # 3. シナリオ: カート重量検知 (スキャン漏れ検知エラー #12085)
    weight_queries = [
        "カートに商品入れたらスキャン漏れって怒られてピーピー鳴る",
        "カゴに商品を入れたのに重量エラーでスマートカートが止まります",
    ]
    for q in weight_queries:
        res = run_aiops_pipeline(q, session_id=f"test-weight-{hash(q)}")
        assert res["status"] == "success"
        assert res["domain"] == "pos"
        assert res["assigned_agent"] == "pos_specialist"
        assert res["tickets"][0]["ticket_id"] == "#12085"
        assert "重量センサー" in res["answer"] or "スキャン" in res["answer"]
        assert "ゼロ点" in res["answer"] or "キャリブレーション" in res["answer"]

    # 4. シナリオ: 店舗ネットワーク (検品ハンディWi-Fi切断 #11980)
    net_queries = [
        "裏のバックヤードのネットが繋がらないです",
        "検品ハンディや品出しタブレットが圏外でオフラインになって使えない",
    ]
    for q in net_queries:
        res = run_aiops_pipeline(q, session_id=f"test-net-{hash(q)}")
        assert res["status"] == "success"
        assert res["domain"] == "network"
        assert res["assigned_agent"] == "network_specialist"
        assert res["tickets"][0]["ticket_id"] == "#11980"
        assert "ネットワークスイッチ" in res["answer"] or "スイッチ" in res["answer"]

    # 5. シナリオ: 発注管理 (生鮮品AI自動発注未反映 #11403)
    order_queries = [
        "今日の野菜や魚の発注データが基幹に反映されてない",
        "生鮮品の自動発注がEDIで止まってて届いてないみたい",
    ]
    for q in order_queries:
        res = run_aiops_pipeline(q, session_id=f"test-order-{hash(q)}")
        assert res["status"] == "success"
        assert res["domain"] == "order"
        assert res["assigned_agent"] == "order_specialist"
        assert res["tickets"][0]["ticket_id"] == "#11403"
        assert "差分再送" in res["answer"] or "再送ジョブ" in res["answer"] or "OrderHub" in res["answer"]

    # 6. シナリオ: カメラビジュアル診断 (#12350)
    camera_queries = [
        "店頭機器の写真を撮ったので見てください",
        "通信レシーバーのLEDランプが赤点滅しています",
    ]
    for q in camera_queries:
        res = run_aiops_pipeline(q, session_id=f"test-cam-{hash(q)}")
        assert res["status"] == "success"
        assert res["domain"] == "pos"
        assert res["assigned_agent"] == "pos_specialist"
        assert res["tickets"][0]["ticket_id"] == "#12350"
        assert "GATE-AP-02" in res["answer"] or "再起動" in res["answer"]




