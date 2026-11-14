"""続けて質問の候補ごとの専用回答と、他ドメイン語を含む候補発話のルーティングを検証する."""

import pytest

import app.agent as ag
from app.agent import run_aiops_pipeline
from app.followup_answers import SPECIFIC_FOLLOWUP_ANSWERS

ESL_PROMPT = "惣菜売場の電子棚札（ESL）で夕方の値下げ価格が反映されず、値引き表示が変わりません。基地局の異常でしょうか"
CART_PROMPT = "東店で、スマートカートのスキャン漏れ検知エラーが頻発してカートが止まります"
GATE_PROMPT = "専用ゲートの2番ゲートでERR-GATE-408が出てカートが通れません"
NETWORK_PROMPT = "バックヤードのWi-Fiが繋がらずハンディ端末が使えません。PoEスイッチのランプが消えています"
INFRA_PROMPT = "在庫同期サーバーのCPU使用率が100%になり、APIが504 Gateway Timeoutを返しています"
ORDER_PROMPT = "生鮮品の自動発注がEDI-ERR-302で止まり、OrderHubで差分再送も失敗しています"


@pytest.fixture(autouse=True)
def _reset_context():
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None
    yield
    ag.SESSION_CONTEXT.clear()
    ag.LATEST_SESSION_CONTEXT = None


def _walk(sid: str, *turns: str) -> dict:
    res = {}
    for t in turns:
        res = run_aiops_pipeline(t, session_id=sid)
    return res


# (シナリオ初回, 候補に到達するまでの更問い, 選ぶ候補, 回答に含まれるべき語, 期待ドメイン)
CASES = [
    (CART_PROMPT, ["ゼロ点リセットを行ってもエラーが消えません"], "アテンダント端末で『保守中』に切り替える方法は？", "カート管理", "pos"),
    (CART_PROMPT, ["お客様の購入商品を予備カートへ移し替える手順は？"], "該当カートを『保守中』にする手順は？", "カート管理", "pos"),
    (GATE_PROMPT, ["スマートストア有人サポートデスクへ連絡する"], "アテンダント端末でのゲート休止操作手順は？", "ゲート管理", "pos"),
    (GATE_PROMPT, ["スマートストア有人サポートデスクへ連絡する"], "隣接ゲートへの誘導手順を確認する", "1番ゲート", "pos"),
    (ESL_PROMPT, ["再起動しても値引き表示が変わりません"], "緊急用値引シールの貼付ルールを確認する", "パッケージ右上", "pos"),
    (ESL_PROMPT, ["再起動しても値引き表示が変わりません"], "別の売場（鮮魚・精肉）の棚札状態を確認する", "未同期", "pos"),
    (ESL_PROMPT, ["紙の値札や手動値引きシールへの一時切り替え手順は？"], "POSレジ側の売価が正しいか確認する方法は？", "価格照会", "pos"),
    (NETWORK_PROMPT, ["PoEスイッチを再起動しても無線APのランプが点灯しません"], "ONU（光回線装置）のアラームランプが赤点滅している時は？", "ONU", "network"),
    (INFRA_PROMPT, ["Podを再起動しても数分でCPUが100%に再上昇します"], "デッドレターキューに退避されたメッセージの再処理方法は？", "100件", "infra"),
]


@pytest.mark.parametrize(("prompt", "path", "candidate", "keyword", "domain"), CASES)
def test_candidate_gets_specific_answer(prompt, path, candidate, keyword, domain):
    """候補を選ぶと、その候補専用の回答（キーワードを含む）が返り、直前と異なる."""
    sid = f"spec-{hash(candidate)}"
    prev = _walk(sid, prompt, *path)
    assert candidate in prev["suggested_followups"], prev["suggested_followups"]
    res = run_aiops_pipeline(candidate, session_id=sid)
    assert res["domain"] == domain
    assert res.get("clarification") is None
    assert keyword in res["answer"]
    assert res["answer"] != prev["answer"]
    assert res["matched_followup"] == candidate


def test_voice_variant_of_other_domain_candidate_stays_in_domain():
    """「鮮魚・精肉」を含む候補の言い換えでも、発注の担当に切り替えず候補どおり回答する."""
    sid = "spec-fish"
    _walk(sid, ESL_PROMPT, "再起動しても値引き表示が変わりません")
    res = run_aiops_pipeline("えーと 別の売場の鮮魚精肉の棚札状態", session_id=sid)
    assert res["domain"] == "pos"
    assert res["matched_followup"] == "別の売場（鮮魚・精肉）の棚札状態を確認する"
    assert "未同期" in res["answer"]


def test_unrelated_other_domain_utterance_is_not_matched_to_candidate():
    """候補と似ていない他ドメインの発話は、候補に割り当てない."""
    sid = "spec-order-switch"
    _walk(sid, ESL_PROMPT, "再起動しても値引き表示が変わりません")
    res = run_aiops_pipeline("青果の発注データが送れない", session_id=sid)
    assert res["matched_followup"] is None


def test_order_deadline_candidate():
    """発注締切後の候補に、緊急手動発注の専用回答が返る."""
    sid = "spec-order"
    # 候補「発注締切（9:00）を過ぎた場合…」は代替（workaround）分岐の候補として提示される
    _walk(sid, ORDER_PROMPT, "差分再送ジョブがエラー（EDI-ERR-302）で再失敗した場合は？")
    prev = run_aiops_pipeline("先行して正常分のみ確定送信する操作手順は？", session_id=sid)
    cand = "発注締切（9:00）を過ぎた場合の緊急手動発注方法は？"
    assert cand in prev["suggested_followups"], prev["suggested_followups"]
    res = run_aiops_pipeline(cand, session_id=sid)
    assert res["domain"] == "order"
    assert "緊急手動発注" in res["answer"]
    assert res.get("clarification") is None


def test_after_resolved_status_case_search_and_close():
    """復旧報告後の3候補（稼働状態・事例検索・完了）がそれぞれ専用回答になる."""
    sid = "spec-resolved"
    _walk(sid, ESL_PROMPT)
    resolved = run_aiops_pipeline("直りました、ありがとうございます", session_id=sid)
    assert "他の売場・設備の稼働状態を確認する" in resolved["suggested_followups"]
    snap = (dict(ag.SESSION_CONTEXT), ag.LATEST_SESSION_CONTEXT)

    status = run_aiops_pipeline("他の売場・設備の稼働状態を確認する", session_id=sid)
    assert "稼働状況" in status["answer"]

    ag.SESSION_CONTEXT.clear()
    ag.SESSION_CONTEXT.update(snap[0])
    ag.LATEST_SESSION_CONTEXT = snap[1]
    cases = run_aiops_pipeline("事例データベースを検索する", session_id=sid)
    assert "【検索結果】" in cases["answer"]
    assert "#" in cases["answer"]  # 実際の過去チケット番号が含まれる

    ag.SESSION_CONTEXT.clear()
    ag.SESSION_CONTEXT.update(snap[0])
    ag.LATEST_SESSION_CONTEXT = snap[1]
    close = run_aiops_pipeline("対応を完了する", session_id=sid)
    assert close["answer"].startswith("対応を完了しました")
    assert close["answer"] != resolved["answer"]


def test_generic_detail_request_not_forced_onto_unrelated_candidate():
    """「レンズを拭くときの注意点を詳しく」は内容の異なる『保守中にする手順』に割り当てない."""
    from app.followup_matcher import match_followup_question

    cands = ["該当カートを『保守中』にする手順は？", "スマートストア保守サポートへ修理を依頼する", "直りました、ありがとうございます"]
    res = match_followup_question("レンズを拭くときの注意点を詳しく教えて", cands, threshold=0.40)
    assert res["assigned"] is False


def test_specific_answers_have_distinct_followups():
    """専用回答の次の候補に、自分自身の候補を含めない（同じ回答のループを防ぐ）."""
    for cand, (_, _, followups) in SPECIFIC_FOLLOWUP_ANSWERS.items():
        assert cand not in followups, cand
