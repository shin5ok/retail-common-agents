"""音声の質問・更問いを「続けて質問」候補に照合して正しく回答することの単体テスト."""

import copy
import re

import pytest

import app.agent as ag
from app.okf_manager import get_okf_manager


def _snapshot():
    return copy.deepcopy(ag.SESSION_CONTEXT), copy.deepcopy(ag.LATEST_SESSION_CONTEXT)


def _restore(snap):
    ag.SESSION_CONTEXT.clear()
    ag.SESSION_CONTEXT.update(copy.deepcopy(snap[0]))
    ag.LATEST_SESSION_CONTEXT = copy.deepcopy(snap[1])


def _voice_variants(c: str) -> list[str]:
    """音声認識で起こりがちな揺れ（言いよどみ・言い換え・途中で途切れ）を模した発話."""
    base = re.sub(r"[？?。、「」『』（）()・]", "", c)
    rephrase = re.sub(r"する$", "したい", base)
    if c.endswith(("は？", "?")):
        rephrase = re.sub(r"は$", "", base) + "を教えて"
    return ["えーと " + base, rephrase, base[: max(6, int(len(base) * 0.6))]]


@pytest.fixture(autouse=True)
def _reset_context():
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None
    yield
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None


def _walk_two_levels():
    """全シナリオについて候補を2階層たどり (直前結果, 直前スナップショット, 候補, 候補の回答結果) を返す."""
    for uc in get_okf_manager().get_all_use_cases():
        ag.SESSION_CONTEXT.clear()
        ag.LATEST_SESSION_CONTEXT = None
        sid = f"t-{uc.id}"
        r0 = ag.run_aiops_pipeline(uc.default_prompt, session_id=sid)
        level = [(r0, _snapshot())]
        for _ in range(2):
            nxt = []
            for prev, snap in level:
                for cand in prev["suggested_followups"]:
                    _restore(snap)
                    r = ag.run_aiops_pipeline(cand, session_id=sid)
                    yield sid, prev, snap, cand, r
                    nxt.append((r, _snapshot()))
            level = nxt


def test_exact_followup_never_repeats_previous_answer():
    for _sid, prev, _snap, cand, r in _walk_two_levels():
        assert r["answer"] != prev["answer"], f"同じ回答の繰り返し: {cand}"


def test_voice_variants_get_same_answer_as_matched_option():
    for sid, _prev, snap, cand, r in _walk_two_levels():
        for v in _voice_variants(cand):
            _restore(snap)
            rv = ag.run_aiops_pipeline(v, session_id=sid)
            assert rv["answer"] == r["answer"] or rv["clarification"], (
                f"候補「{cand}」の言い換え「{v}」で別の回答になった"
            )


def test_unknown_intent_followup_answers_without_asking_back():
    """デモ向け: 意図不明の更問いでも聞き返さず、同じ担当のまま現在の手順で回答し、候補を出し続ける."""
    ag.run_aiops_pipeline(get_okf_manager().get_use_case("scenario-network").default_prompt, session_id="t-unk")
    r = ag.run_aiops_pipeline("うーん、それで？", session_id="t-unk")
    assert not r["clarification"]
    assert not r["answer"].startswith("【確認】")
    assert "意図を特定できませんでした" not in r["answer"]
    assert r["assigned_agent"] == "network_specialist"
    assert r["answer"].startswith("【対応指示】")
    assert r["suggested_followups"]


def test_ordinal_selection_picks_option():
    first = ag.run_aiops_pipeline(get_okf_manager().get_use_case("scenario-network").default_prompt, session_id="t-ord")
    r = ag.run_aiops_pipeline("2番", session_id="t-ord")
    assert r["matched_followup"] == first["suggested_followups"][1]
    assert not r["clarification"]


def test_colloquial_workaround_matches_spare_cart_option():
    ag.run_aiops_pipeline(
        "東店で、スマートカートのスキャン漏れ検知エラーが頻発してカートが止まります",
        session_id="t-cart",
    )
    ag.run_aiops_pipeline("ゼロ点リセットを行ってもエラーが消えません", session_id="t-cart")
    r = ag.run_aiops_pipeline("いまお客さん待たせてるから急ぎの代替方法教えて", session_id="t-cart")
    assert r["matched_followup"] == "お客様の購入商品を予備カートへ移し替える手順は？"
    assert "予備カート" in r["answer"] or "乗換" in r["answer"]


def test_voice_matching_reason_is_visible_in_reasoning():
    ag.run_aiops_pipeline(get_okf_manager().get_use_case("scenario-order").default_prompt, session_id="t-rs")
    r = ag.run_aiops_pipeline("えーと 受発注EDI運用サポートデスクへ連絡したい", session_id="t-rs")
    intent_step = next(s for s in r["reasoning_steps"] if s["type"] == "intent_classification")
    assert "続けて質問" in intent_step["detail"]
    assert r["matched_followup"] == "受発注・EDI運用サポートデスクへ連絡する"
