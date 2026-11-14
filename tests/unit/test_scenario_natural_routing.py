"""ワンクリックでお試しの各シナリオに、自然言語の相談で確実にルーティングされることを検証する.

直前の会話（なし / 各シナリオ実行後）に関係なく、OKF が特定したケースの担当・チケットに振られ、
「ご質問の意図を特定できませんでした」の聞き返しにならないことを保証する。
"""

import pytest

import app.agent as ag
from app.agent import run_aiops_pipeline
from app.okf_manager import get_okf_manager

NATURAL_PHRASES: dict[str, list[str]] = {
    "scenario-infra": [
        "在庫サーバーのCPUが100%になってる",
        "APサーバーが重くて504が出る",
        "サーバーが高負荷でタイムアウトする",
    ],
    "scenario-camera-diagnostic": [
        "ゲートのランプを写真に撮ったので見てください",
        "エラー画面を撮影しました、画像診断して",
    ],
    "scenario-pos-weight": [
        "カートのスキャン漏れエラーが頻発する",
        "スマートカートが重量検知エラーで止まる",
        "カートが重さのエラーで止まる",
    ],
    "scenario-pos-esl": [
        "電子棚札の値下げが更新されていない",
        "惣菜の値札が半額にならない",
        "ESLが定価のまま変わらない",
        "棚札の値段が変わらない",
    ],
    "scenario-network": [
        "バックヤードのWIFIつながらない",
        "検品ハンディがオフラインになった",
        "バックヤードのネットが繋がらない",
    ],
    "scenario-order": [
        "生鮮品の自動発注が反映されていない",
        "青果の発注データが基幹に届いていない",
        "発注データがエラーで送れない",
    ],
    "scenario-pos": [
        "専用ゲートを通るとフリーズする",
        "2番ゲートで通過エラーが出る",
        "スマートカートでゲートが開かない",
    ],
}

_USE_CASES = {uc.id: uc for uc in get_okf_manager().get_all_use_cases()}
_PREV_IDS = [None, *_USE_CASES.keys()]
_CASES = [(target, q) for target, phrases in NATURAL_PHRASES.items() for q in phrases]


@pytest.fixture(autouse=True)
def _reset_context():
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None
    yield
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None


def test_all_one_click_scenarios_are_covered():
    """ワンクリックシナリオを追加したら、自然言語フレーズも追加する."""
    assert set(NATURAL_PHRASES) == set(_USE_CASES)


@pytest.mark.parametrize("prev_id", _PREV_IDS)
@pytest.mark.parametrize("target_id, query", _CASES)
def test_natural_language_routes_to_scenario(target_id, query, prev_id):
    expected = _USE_CASES[target_id]
    sess = "natural-routing"
    if prev_id:
        run_aiops_pipeline(_USE_CASES[prev_id].default_prompt, session_id=sess)

    res = run_aiops_pipeline(query, session_id=sess)

    top_ticket = res["tickets"][0]["ticket_id"] if res["tickets"] else None
    assert res["assigned_agent"] == expected.agent_id
    assert top_ticket == expected.ticket_id
    assert res.get("clarification") is None
    assert "ご質問の意図を特定できませんでした" not in res["answer"]


def test_esl_after_gate_scenario_starts_esl_troubleshooting():
    """ゲート対応中に「電子棚札の値下げが更新されていない」と入力すると、ESL 基地局の初回対応手順が返る."""
    sess = "gate-then-esl"
    run_aiops_pipeline(_USE_CASES["scenario-pos"].default_prompt, session_id=sess)
    res = run_aiops_pipeline("電子棚札の値下げが更新されていない", session_id=sess)
    assert res["tickets"][0]["ticket_id"] == "#12110"
    assert "ESL基地局" in res["answer"]


def test_followup_candidate_selection_still_continues():
    """シナリオ後に提示された候補をそのまま選んだ場合は、従来どおり同じ担当の更問いとして継続する."""
    sess = "esl-followup"
    first = run_aiops_pipeline(_USE_CASES["scenario-pos-esl"].default_prompt, session_id=sess)
    cand = first["suggested_followups"][0]
    res = run_aiops_pipeline(cand, session_id=sess)
    assert res["assigned_agent"] == "pos_specialist"
    assert res["matched_followup"] == cand
