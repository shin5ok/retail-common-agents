"""Unit tests for OKF (Open Knowledge Format) use case management & fuzzy matching.

Verifies:
1. OKF loading from okf/use_cases/*.md
2. High accuracy fuzzy matching on casual, fragmented, and colloquial expressions ("適当な文章")
3. End-to-end routing and answer generation via run_aiops_pipeline
"""

import pytest
from app.agent import run_aiops_pipeline
from app.okf_manager import get_okf_manager


def test_okf_loading():
    """okf/use_cases/ 配下の全ユースケースが正しくロードされることをテスト."""
    mgr = get_okf_manager()
    use_cases = mgr.get_all_use_cases()
    assert len(use_cases) >= 6

    ids = {uc.id for uc in use_cases}
    expected_ids = {
        "scenario-infra",
        "scenario-pos-esl",
        "scenario-pos-weight",
        "scenario-network",
        "scenario-order",
        "scenario-camera-diagnostic",
    }
    assert expected_ids.issubset(ids)

    # シナリオ辞書生成 (4大スペシャリスト + 電子棚札)
    scenarios = mgr.get_scenarios()
    assert len(scenarios) == 5
    scenario_ids = [s["id"] for s in scenarios]
    assert scenario_ids == [
        "scenario-infra",
        "scenario-order",
        "scenario-network",
        "scenario-pos",
        "scenario-pos-esl",
    ]
    assert scenarios[0]["agent_name"] == "インフラ スペシャリスト"
    assert scenarios[1]["agent_name"] == "発注管理 スペシャリスト"
    assert scenarios[2]["agent_name"] == "店舗ネットワーク スペシャリスト"
    assert scenarios[3]["agent_name"] == "POS スペシャリスト"
    assert scenarios[4]["agent_id"] == "pos_specialist"


def test_okf_casual_queries_matching():
    """現場スタッフの『適当な文章』（短文・崩れた口語・曖昧表現）での照合精度テスト."""
    mgr = get_okf_manager()

    casual_test_cases = [
        # 1. インフラ
        ("重すぎ", "scenario-infra", "infra"),
        ("サーバー落ちた", "scenario-infra", "infra"),
        ("504出てる", "scenario-infra", "infra"),
        ("CPU張り付いてる", "scenario-infra", "infra"),
        # 2. 電子棚札
        ("値札変", "scenario-pos-esl", "pos"),
        ("値段変わらん", "scenario-pos-esl", "pos"),
        ("惣菜の値札がおかしい", "scenario-pos-esl", "pos"),
        ("半額にならない", "scenario-pos-esl", "pos"),
        # 3. カート重量検知
        ("ピーピーうるさい", "scenario-pos-weight", "pos"),
        ("スキャン漏れって怒られる", "scenario-pos-weight", "pos"),
        ("カゴに入れたら止まった", "scenario-pos-weight", "pos"),
        ("カートが鳴る", "scenario-pos-weight", "pos"),
        # 4. 店舗NW
        ("裏のwifi死んだ", "scenario-network", "network"),
        ("ネット繋がらん", "scenario-network", "network"),
        ("ハンディ圏外", "scenario-network", "network"),
        ("バーコード読めない", "scenario-network", "network"),
        # 5. 発注管理
        ("発注届かん", "scenario-order", "order"),
        ("魚の発注届いてない", "scenario-order", "order"),
        ("野菜の発注データがない", "scenario-order", "order"),
        ("EDI止まってる", "scenario-order", "order"),
        # 6. カメラ画像AIビジュアル診断
        ("写真見て", "scenario-camera-diagnostic", "pos"),
        ("カメラで見て", "scenario-camera-diagnostic", "pos"),
        ("ランプ赤点滅", "scenario-camera-diagnostic", "pos"),
        ("写真撮った", "scenario-camera-diagnostic", "pos"),
    ]

    for query, expected_id, expected_domain in casual_test_cases:
        matched_uc, score = mgr.match_use_case(query)
        assert matched_uc is not None, f"Failed to match query: '{query}'"
        assert (
            matched_uc.id == expected_id
        ), f"Query '{query}' matched '{matched_uc.id}' instead of '{expected_id}' (score: {score})"
        assert matched_uc.domain == expected_domain
        assert score >= 0.70, f"Query '{query}' had low score {score}"


def test_e2e_casual_queries_pipeline():
    """適当な文章を入力した際の E2E パイプライン（エージェント判定・チケット特定・回答生成）テスト."""
    cases = [
        # (クエリ, 期待エージェント, 期待チケットID, 期待回答キーワード)
        ("重すぎ", "infra_specialist", "#10892", "ローリング再起動"),
        ("値札変", "pos_specialist", "#12110", "ESL"),
        ("ピーピーうるさい", "pos_specialist", "#12085", "重量センサー"),
        ("裏のwifi死んだ", "network_specialist", "#11980", "ネットワークスイッチ"),
        ("発注届かん", "order_specialist", "#11403", "差分再送"),
        ("写真見て", "pos_specialist", "#12350", "GATE-AP-02"),
    ]

    for query, expected_agent, expected_ticket, answer_keyword in cases:
        res = run_aiops_pipeline(query, session_id=f"casual-{hash(query)}")
        assert res["status"] == "success"
        assert (
            res["assigned_agent"] == expected_agent
        ), f"Query '{query}' routed to {res['assigned_agent']} (expected {expected_agent})"
        assert (
            res["tickets"][0]["ticket_id"] == expected_ticket
        ), f"Query '{query}' matched ticket {res['tickets'][0]['ticket_id']} (expected {expected_ticket})"
        assert (
            answer_keyword in res["answer"]
        ), f"Query '{query}' answer did not contain '{answer_keyword}': {res['answer']}"
        # Chain of ThoughtにOKF特定ログが含まれること
        assert any("OKFナレッジ" in step.get("detail", "") for step in res["reasoning_steps"])
