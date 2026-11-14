"""更問い（Follow-up）送信時にエージェントが不必要に切り替わらないことを検証する単体テスト."""

from __future__ import annotations

import pytest
from app.agent import run_aiops_pipeline


def test_infra_followup_stays_with_infra_specialist():
    """インフラ障害の初期問い合わせ後、更問いが infra_specialist に維持されることを検証."""
    session_id = "test-session-infra-followup-1"

    # 1st turn: インフラ障害の相談
    turn1 = run_aiops_pipeline(
        query="APサーバー（ap-inv-prod-02）のCPUが100%になって504エラーが出ています",
        session_id=session_id,
    )
    assert turn1["assigned_agent"] == "infra_specialist"
    assert turn1["domain"] == "infra"
    assert "suggested_followups" in turn1
    followups = turn1["suggested_followups"]
    assert len(followups) > 0

    # 2nd turn: サジェストされた候補そのままの更問い
    turn2 = run_aiops_pipeline(
        query=followups[0],
        session_id=session_id,
    )
    assert turn2["assigned_agent"] == "infra_specialist", f"Expected infra_specialist but got {turn2['assigned_agent']}"
    assert turn2["domain"] == "infra"

    # 3rd turn: 口語・短文での更問い「詳しいコマンド手順を教えて」
    turn3 = run_aiops_pipeline(
        query="コマンドの手順を詳しく教えて",
        session_id=session_id,
    )
    assert turn3["assigned_agent"] == "infra_specialist", f"Expected infra_specialist but got {turn3['assigned_agent']}"
    assert turn3["domain"] == "infra"

    # 4th turn: 「直りました、ありがとうございます」で解決報告
    turn4 = run_aiops_pipeline(
        query="直りました、ありがとうございます",
        session_id=session_id,
    )
    assert turn4["assigned_agent"] == "infra_specialist", f"Expected infra_specialist but got {turn4['assigned_agent']}"
    assert turn4["domain"] == "infra"


def test_order_followup_stays_with_order_specialist():
    """受発注障害の初期問い合わせ後、更問いが order_specialist に維持されることを検証."""
    session_id = "test-session-order-followup-1"

    # 1st turn: 生鮮品発注障害の相談
    turn1 = run_aiops_pipeline(
        query="今日の生鮮品（青果・鮮魚・精肉）のAI自動発注データが基幹システムに未反映です (EDI-ERR-302)",
        session_id=session_id,
    )
    assert turn1["assigned_agent"] == "order_specialist"
    assert turn1["domain"] == "order"

    # 2nd turn: 口語での未解決更問い
    turn2 = run_aiops_pipeline(
        query="再送ジョブを試したけどエラー消えないです",
        session_id=session_id,
    )
    assert turn2["assigned_agent"] == "order_specialist", f"Expected order_specialist but got {turn2['assigned_agent']}"
    assert turn2["domain"] == "order"

    # 3rd turn: エスカレーションの更問い
    turn3 = run_aiops_pipeline(
        query="サポートデスクへ連絡してください",
        session_id=session_id,
    )
    assert turn3["assigned_agent"] == "order_specialist", f"Expected order_specialist but got {turn3['assigned_agent']}"
    assert turn3["domain"] == "order"


def test_network_followup_stays_with_network_specialist():
    """ネットワーク障害の初期問い合わせ後、更問いが network_specialist に維持されることを検証."""
    session_id = "test-session-net-followup-1"

    # 1st turn: 店舗Wi-Fi・PoE障害
    turn1 = run_aiops_pipeline(
        query="バックヤードのPoEスイッチが落ちてWi-Fiが繋がらないです",
        session_id=session_id,
    )
    assert turn1["assigned_agent"] == "network_specialist"
    assert turn1["domain"] == "network"

    # 2nd turn: ランプ確認の手順更問い
    turn2 = run_aiops_pipeline(
        query="LEDランプの確認方法を詳しく知りたい",
        session_id=session_id,
    )
    assert turn2["assigned_agent"] == "network_specialist", f"Expected network_specialist but got {turn2['assigned_agent']}"
    assert turn2["domain"] == "network"

    # 3rd turn: 未復旧の報告
    turn3 = run_aiops_pipeline(
        query="再起動してもランプがつかないです",
        session_id=session_id,
    )
    assert turn3["assigned_agent"] == "network_specialist", f"Expected network_specialist but got {turn3['assigned_agent']}"
    assert turn3["domain"] == "network"


def test_explicit_topic_switch_changes_agent():
    """「別の質問ですが」など明確なトピック転換時は別エージェントに切り替わることを検証."""
    session_id = "test-session-topic-switch-1"

    # 1st turn: ネットワーク
    turn1 = run_aiops_pipeline(
        query="バックヤードのPoEスイッチが落ちてWi-Fiが繋がらないです",
        session_id=session_id,
    )
    assert turn1["assigned_agent"] == "network_specialist"

    # 2nd turn: 明示的に別の質問として電子棚札の障害を相談
    turn2 = run_aiops_pipeline(
        query="別の質問ですが、惣菜の電子棚札の値下げ価格が変わりません",
        session_id=session_id,
    )
    assert turn2["assigned_agent"] == "pos_specialist"
    assert turn2["domain"] == "pos"


def test_scenario_switching_routes_to_all_four_specialists_consistently():
    """発注シナリオ実行後でも、インフラ・ネットワーク・POSの各ケースが適切に各専門エージェントへルーティングされることを検証."""
    from app.okf_manager import get_okf_manager

    mgr = get_okf_manager()
    scenarios = mgr.get_scenarios()
    sc_dict = {sc["id"]: sc for sc in scenarios}

    # 1. 発注シナリオ（order_specialist）を同一セッションで先行実行
    sess = "test-shared-scenario-session"
    res_order = run_aiops_pipeline(sc_dict["scenario-order"]["prompt"], session_id=sess)
    assert res_order["assigned_agent"] == "order_specialist"

    # 2. 同一セッションのままインフラシナリオを実行 -> infra_specialist に正しく切り替わる
    res_infra = run_aiops_pipeline(sc_dict["scenario-infra"]["prompt"], session_id=sess)
    assert res_infra["assigned_agent"] == "infra_specialist", f"Expected infra_specialist but got {res_infra['assigned_agent']}"
    assert res_infra["domain"] == "infra"

    # 3. 同一セッションのままネットワークシナリオを実行 -> network_specialist に正しく切り替わる
    res_net = run_aiops_pipeline(sc_dict["scenario-network"]["prompt"], session_id=sess)
    assert res_net["assigned_agent"] == "network_specialist", f"Expected network_specialist but got {res_net['assigned_agent']}"
    assert res_net["domain"] == "network"

    # 4. 同一セッションのままPOSシナリオを実行 -> pos_specialist に正しく切り替わる
    res_pos = run_aiops_pipeline(sc_dict["scenario-pos"]["prompt"], session_id=sess)
    assert res_pos["assigned_agent"] == "pos_specialist", f"Expected pos_specialist but got {res_pos['assigned_agent']}"
    assert res_pos["domain"] == "pos"


def test_scenario_dedicated_sessions():
    """シナリオごとに独立セッションIDが発行された場合も確実に各専門エージェントにルーティングされることを検証."""
    from app.okf_manager import get_okf_manager

    mgr = get_okf_manager()
    for sc in mgr.get_scenarios():
        res = run_aiops_pipeline(sc["prompt"], session_id=f"scenario-{sc['id']}-test")
        assert res["assigned_agent"] == sc["agent_id"], f"Scenario {sc['id']} expected {sc['agent_id']} but got {res['assigned_agent']}"

