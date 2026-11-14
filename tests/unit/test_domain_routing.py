"""ネットワーク系の相談がネットワーク担当に振られ、既存シナリオの担当が変わらないことを検証する."""

import pytest

import app.agent as ag
from app.agent import run_aiops_pipeline
from app.okf_manager import get_okf_manager


@pytest.fixture(autouse=True)
def _reset_context():
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None
    yield
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None


@pytest.mark.parametrize(
    "query",
    [
        "店のインターネットが繋がらない",
        "ONUのランプが赤く点滅している",
        "光回線が切れたみたい",
        "通信回線が遅い",
        "AP-BY-01のランプが消えている",
    ],
)
def test_network_queries_route_to_network_specialist(query):
    res = run_aiops_pipeline(query, session_id=f"route-{query}")
    assert res["assigned_agent"] == "network_specialist"


def test_all_scenarios_keep_their_agent():
    """既存デモシナリオの初回問い合わせは、定義どおりの担当に振られる（POS のゲート無線シナリオ含む）."""
    for uc in get_okf_manager().get_all_use_cases():
        ag.SESSION_CONTEXT.clear()
        ag.LATEST_SESSION_CONTEXT = None
        res = run_aiops_pipeline(uc.default_prompt, session_id=f"route-{uc.id}")
        assert res["assigned_agent"] == uc.agent_id, uc.id


def test_pos_session_switches_to_network_on_wifi_query():
    """直前がPOS対応のセッションであっても、「バックヤードのWIFIつながらない」等の相談時はPOSで抱え込まずnetwork_specialistに切り替わること."""
    sess = "test-pos-then-wifi"
    # 1. POSカメラ診断やゲートエラーを相談（担当は pos_specialist）
    pos_res = run_aiops_pipeline(
        "専用ゲート通過時に通信タイムアウトで画面が止まりました (ERR-GATE-408)",
        session_id=sess,
    )
    assert pos_res["assigned_agent"] == "pos_specialist"
    assert pos_res["domain"] == "pos"

    # 2. 同じセッションのまま音声入力等で「バックヤードのWIFIつながらない」と相談
    net_res = run_aiops_pipeline(
        "バックヤードのWIFIつながらない",
        session_id=sess,
    )
    # POSの更問い「【確認】先ほどと同じご案内になるため…」ではなく、店舗ネットワーク担当に切り替わること
    assert net_res["assigned_agent"] == "network_specialist"
    assert net_res["domain"] == "network"
    assert net_res.get("clarification") is None
    assert "SW-BY-01" in net_res["answer"] or "ネットワークスイッチ" in net_res["answer"] or "給電" in net_res["answer"]


@pytest.mark.parametrize(
    "query, expected_agent, expected_domain",
    [
        ("サーバーのCPUが100%で高負荷です", "infra_specialist", "infra"),
        ("生鮮品の発注データが反映されていません", "order_specialist", "order"),
        ("バックヤードのWIFIがつながらない", "network_specialist", "network"),
    ],
)
def test_pos_session_switches_to_other_specialists_on_domain_keywords(query, expected_agent, expected_domain):
    """POSセッション後でも他ドメインの強いキーワードがあれば適切に新担当へ切り替わること."""
    sess = f"test-switch-{expected_domain}"
    run_aiops_pipeline("電子棚札の価格が更新されません", session_id=sess)
    res = run_aiops_pipeline(query, session_id=sess)
    assert res["assigned_agent"] == expected_agent
    assert res["domain"] == expected_domain
    assert res.get("clarification") is None



def test_is_followup_flag_for_routing_animation():
    """画面のルーティング演出の出し分け用に、続きの質問かどうか（is_followup）を返すこと."""
    sess = "followup-flag"
    first = run_aiops_pipeline("バックヤードのWIFIつながらない", session_id=sess)
    assert first["is_followup"] is False

    follow = run_aiops_pipeline("もう少し詳しく", session_id=sess)
    assert follow["is_followup"] is True
    assert follow["assigned_agent"] == first["assigned_agent"]

    resolved = run_aiops_pipeline("直りました、ありがとうございます", session_id=sess)
    assert resolved["is_followup"] is True

    switched = run_aiops_pipeline("サーバーのCPUが100%で高負荷です", session_id=sess)
    assert switched["is_followup"] is False
    assert switched["assigned_agent"] == "infra_specialist"
