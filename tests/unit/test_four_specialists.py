"""スライド準拠 4大スペシャリスト（AGENT 01〜04）の連携・動作検証テスト.

スライド仕様 (Slide 24):
- AGENT 01: インフラ スペシャリスト (infra_specialist)
- AGENT 02: 発注管理 スペシャリスト (order_specialist)
- AGENT 03: 店舗ネットワーク スペシャリスト (network_specialist)
- AGENT 04: POS スペシャリスト (pos_specialist)
"""

import pytest
from fastapi.testclient import TestClient

from app.agent import (
    infra_specialist,
    network_specialist,
    order_specialist,
    pos_specialist,
    root_agent,
    run_aiops_pipeline,
)
from app.fast_api_app import app
from app.okf_manager import get_okf_manager


@pytest.fixture
def client():
    return TestClient(app)


def test_specialists_definitions():
    """4大スペシャリストの名前・ラベル・登録状態を検証."""
    assert infra_specialist.name == "infra_specialist"
    assert order_specialist.name == "order_specialist"
    assert network_specialist.name == "network_specialist"
    assert pos_specialist.name == "pos_specialist"

    # root_agent のサブエージェントに4名が揃っていること
    sub_agent_names = {sub.name for sub in root_agent.sub_agents}
    expected_sub_agents = {
        "infra_specialist",
        "order_specialist",
        "network_specialist",
        "pos_specialist",
    }
    assert expected_sub_agents == sub_agent_names


def test_four_scenarios_endpoint(client):
    """GET /api/scenarios でスライドの4大スペシャリストが正しい順序で返却されること."""
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    scenarios = response.json()

    assert len(scenarios) >= 4

    # AGENT 01: インフラ スペシャリスト
    assert scenarios[0]["id"] == "scenario-infra"
    assert scenarios[0]["agent_id"] == "infra_specialist"
    assert scenarios[0]["agent_name"] == "インフラ スペシャリスト"
    assert "AGENT 01" in scenarios[0]["badge"]

    # AGENT 02: 発注管理 スペシャリスト
    assert scenarios[1]["id"] == "scenario-order"
    assert scenarios[1]["agent_id"] == "order_specialist"
    assert scenarios[1]["agent_name"] == "発注管理 スペシャリスト"
    assert "AGENT 02" in scenarios[1]["badge"]

    # AGENT 03: 店舗ネットワーク スペシャリスト
    assert scenarios[2]["id"] == "scenario-network"
    assert scenarios[2]["agent_id"] == "network_specialist"
    assert scenarios[2]["agent_name"] == "店舗ネットワーク スペシャリスト"
    assert "AGENT 03" in scenarios[2]["badge"]

    # AGENT 04: POS スペシャリスト
    assert scenarios[3]["id"] == "scenario-pos"
    assert scenarios[3]["agent_id"] == "pos_specialist"
    assert scenarios[3]["agent_name"] == "POS スペシャリスト"
    assert "AGENT 04" in scenarios[3]["badge"]


def test_agent01_infra_pipeline():
    """AGENT 01: インフラ スペシャリストのシナリオ推論パイプラインをテスト."""
    query = "在庫・AIカメラ欠品検知APサーバー（ap-inv-prod-02）のCPU使用率が100%に張り付いて504タイムアウトが多発しています。一次対処を教えてください。"
    res = run_aiops_pipeline(query=query, session_id="test-infra-1")

    assert res["assigned_agent"] == "infra_specialist"
    assert "インフラ" in res["assigned_agent_label"]
    assert res["domain"] == "infra"
    assert "#10892" in res["answer"] or "10892" in str(res["tickets"])
    assert len(res["reasoning_steps"]) >= 5
    assert len(res["runbook"]["steps"]) > 0
    assert "SRE" in res["escalation_team"]


def test_agent02_order_pipeline():
    """AGENT 02: 発注管理 スペシャリストのシナリオ推論パイプラインをテスト."""
    query = "今日の生鮮品（青果・鮮魚・精肉）のAI自動発注データが基幹システムに反映されていないのですが、エラーは出ていますか？（EDI-ERR-302）"
    res = run_aiops_pipeline(query=query, session_id="test-order-1")

    assert res["assigned_agent"] == "order_specialist"
    assert "発注管理" in res["assigned_agent_label"]
    assert res["domain"] == "order"
    assert "#11403" in res["answer"] or "11403" in str(res["tickets"])
    assert len(res["reasoning_steps"]) >= 5
    assert "EDI" in res["escalation_team"] or "受発注" in res["escalation_team"]


def test_agent03_network_pipeline():
    """AGENT 03: 店舗ネットワーク スペシャリストのシナリオ推論パイプラインをテスト."""
    query = "西店のバックヤードでネットワークが繋がらない状態です。検品ハンディや品出しタブレットもオフラインになっています。"
    res = run_aiops_pipeline(query=query, session_id="test-net-1")

    assert res["assigned_agent"] == "network_specialist"
    assert "店舗ネットワーク" in res["assigned_agent_label"]
    assert res["domain"] == "network"
    assert "#11980" in res["answer"] or "11980" in str(res["tickets"])
    assert len(res["reasoning_steps"]) >= 5
    assert "ネットワーク保守" in res["escalation_team"]


def test_agent04_pos_pipeline():
    """AGENT 04: POS スペシャリストのシナリオ推論パイプラインをテスト."""
    query = "中央店のスマートカート専用ゲート（2番ゲート）を通過した際、自動決済が完了せず画面がフリーズしています（ERR-GATE-408）。過去に似た事象はありますか？"
    res = run_aiops_pipeline(query=query, session_id="test-pos-1")

    assert res["assigned_agent"] == "pos_specialist"
    assert "POS" in res["assigned_agent_label"]
    assert res["domain"] == "pos"
    assert "#12345" in res["answer"] or "12345" in str(res["tickets"])
    assert len(res["reasoning_steps"]) >= 5
    assert "GATE-AP-02" in res["answer"] or "再起動" in res["answer"]
    assert "サポートデスク" in res["escalation_team"]
