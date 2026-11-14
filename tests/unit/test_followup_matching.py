"""Unit tests for voice followup matching."""

from __future__ import annotations

import pytest
from app.followup_matcher import match_followup_question


@pytest.fixture
def sample_candidates():
    return [
        "ネットワークスイッチを再起動しても無線APのランプが点灯しません",
        "ネットワークスイッチ再起動後のLEDランプ状態確認の詳細手順は？",
        "店舗ネットワーク保守センターへ連絡する",
    ]


def test_match_unresolved_intent(sample_candidates):
    """「再起動しても直らない」などの口語発話が正しく未復旧の質問にマッチすることを検証."""
    res1 = match_followup_question("再起動やったけど直らないよ", sample_candidates)
    assert res1["assigned"] is True
    assert res1["matched"] == "ネットワークスイッチを再起動しても無線APのランプが点灯しません"

    res2 = match_followup_question("ランプつかないです", sample_candidates)
    assert res2["assigned"] is True
    assert res2["matched"] == "ネットワークスイッチを再起動しても無線APのランプが点灯しません"


def test_match_detail_procedure_intent(sample_candidates):
    """「手順を教えて」「やり方詳しく」が詳細手順の質問にマッチすることを検証."""
    res1 = match_followup_question("確認のやり方を詳しく教えて", sample_candidates)
    assert res1["assigned"] is True
    assert res1["matched"] == "ネットワークスイッチ再起動後のLEDランプ状態確認の詳細手順は？"

    res2 = match_followup_question("どこを見ればいいの？", sample_candidates)
    assert res2["assigned"] is True
    assert res2["matched"] == "ネットワークスイッチ再起動後のLEDランプ状態確認の詳細手順は？"


def test_match_escalation_intent(sample_candidates):
    """「人に代わって」「電話したい」がエスカレーションにマッチすることを検証."""
    res1 = match_followup_question("サポートの人に電話で代わってほしい", sample_candidates)
    assert res1["assigned"] is True
    assert res1["matched"] == "店舗ネットワーク保守センターへ連絡する"

    res2 = match_followup_question("保守センターに連絡して", sample_candidates)
    assert res2["assigned"] is True
    assert res2["matched"] == "店舗ネットワーク保守センターへ連絡する"


def test_no_match_for_completely_unrelated(sample_candidates):
    """全く関係ない話題ではマッチせず assigned: False になることを検証."""
    res = match_followup_question("今日のお昼ご飯は何にしようかな", sample_candidates)
    assert res["assigned"] is False
    assert res["matched"] is None


def test_match_infra_candidates():
    candidates = [
        "Podを再起動しても数分でCPUが100%に再上昇します",
        "Pod再起動の具体的なコマンド手順を教えて",
        "クラウドSREオンコールチームへ連絡する",
    ]
    # 未解決
    res1 = match_followup_question("再起動したけどCPU下がらない", candidates)
    assert res1["assigned"] is True
    assert res1["matched"] == candidates[0]

    # コマンド手順
    res2 = match_followup_question("コマンドどう打てばいい？", candidates)
    assert res2["assigned"] is True
    assert res2["matched"] == candidates[1]

    # SRE連絡
    res3 = match_followup_question("SREの人に連絡して", candidates)
    assert res3["assigned"] is True
    assert res3["matched"] == candidates[2]


def test_match_order_candidates():
    candidates = [
        "差分再送ジョブがエラー（EDI-ERR-302）で再失敗した場合は？",
        "エラー対象レコードを一時保留（スキップ）する具体的な画面手順は？",
        "受発注・EDI運用サポートデスクへ連絡する",
    ]
    # スキップ手順
    res1 = match_followup_question("スキップする画面のやり方教えて", candidates)
    assert res1["assigned"] is True
    assert res1["matched"] == candidates[1]

    # 再失敗
    res2 = match_followup_question("再送ジョブまたエラーになった", candidates)
    assert res2["assigned"] is True
    assert res2["matched"] == candidates[0]

