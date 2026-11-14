"""デモ向け: 各シナリオ後の口語的な更問いで「ご質問の意図を特定できませんでした」等の聞き返しが出ないことを検証する."""

import pytest

import app.agent as ag
from app.agent import classify_conversational_followup, run_aiops_pipeline, select_followup_by_ordinal
from app.okf_manager import get_okf_manager

UTTERANCES = [
    "どうすればいい？", "もう少し詳しく", "それでも直らない", "直りました", "ありがとう", "わかりました",
    "次は何をすればいい？", "他に方法はある？", "原因は何？", "どれくらいで直る？", "誰に連絡すればいい？",
    "お客様にはどう説明すればいい？", "やってみます", "再起動しました", "まだダメです", "うーん", "はい",
    "いいえ", "お願いします", "エスカレーションして", "手順をもう一度", "それって何？", "急いでるんだけど",
    "過去に似た事例はある？", "今どういう状況？", "1番", "2番でお願い", "最初のやつ",
]
_USE_CASES = get_okf_manager().get_all_use_cases()


@pytest.fixture(autouse=True)
def _reset_context():
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None
    yield
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None


def _assert_no_ask_back(res):
    assert not res.get("clarification")
    assert "意図を特定できませんでした" not in res["answer"]
    assert "確認させてください" not in res["answer"]
    assert not res["answer"].startswith("【確認】")


@pytest.mark.parametrize("uc", _USE_CASES, ids=lambda u: u.id)
@pytest.mark.parametrize("utterance", UTTERANCES)
def test_colloquial_followup_never_asks_back(uc, utterance):
    first = run_aiops_pipeline(uc.default_prompt, session_id="nc")
    res = run_aiops_pipeline(utterance, session_id="nc")
    _assert_no_ask_back(res)
    assert res["answer"].strip()
    # 雑談的な更問いでも担当は変わらない
    assert res["assigned_agent"] == first["assigned_agent"]


@pytest.mark.parametrize("uc", _USE_CASES, ids=lambda u: u.id)
def test_repeated_same_utterance_does_not_ask_back(uc):
    run_aiops_pipeline(uc.default_prompt, session_id="rp")
    run_aiops_pipeline("もう少し詳しく", session_id="rp")
    res = run_aiops_pipeline("もう少し詳しく", session_id="rp")
    _assert_no_ask_back(res)


def test_yes_after_escalation_question_escalates():
    first = run_aiops_pipeline(get_okf_manager().get_use_case("scenario-network").default_prompt, session_id="y")
    assert "エスカレーションしますか" in first["answer"]
    res = run_aiops_pipeline("はい、お願いします", session_id="y")
    assert "保守センター" in res["answer"]
    assert "要請" in res["answer"] or "手配" in res["answer"]


def test_cause_question_explains_root_cause():
    run_aiops_pipeline(get_okf_manager().get_use_case("scenario-pos-esl").default_prompt, session_id="c")
    res = run_aiops_pipeline("原因は何？", session_id="c")
    assert "原因" in res["answer"]
    assert "#12110" in res["answer"]


def test_contact_question_returns_escalation_team():
    run_aiops_pipeline(get_okf_manager().get_use_case("scenario-order").default_prompt, session_id="ct")
    res = run_aiops_pipeline("誰に連絡すればいい？", session_id="ct")
    assert "#3200" in res["answer"]


@pytest.mark.parametrize(
    "text, idx",
    [("2番でお願い", 1), ("最初のやつ", 0), ("最後の", 2), ("真ん中で", 1), ("3番目をお願いします", 2)],
)
def test_ordinal_variants(text, idx):
    cands = ["A", "B", "C"]
    assert select_followup_by_ordinal(text, cands) == cands[idx]


@pytest.mark.parametrize(
    "text, kind",
    [
        ("原因は何？", "cause"), ("過去に似た事例はある？", "history"), ("今どういう状況？", "status"),
        ("お客様にはどう説明すればいい？", "customer"), ("誰に連絡すればいい？", "contact"),
        ("いいえ", "no"), ("はい", "yes"), ("やってみます", "ack"), ("うーん", "next"),
    ],
)
def test_classify_conversational_followup(text, kind):
    assert classify_conversational_followup(text) == kind
