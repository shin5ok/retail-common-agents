"""音声入力テキストとサジェストされた『続けて質問（suggested_followups）』の意味照合モジュール.

口語・短文・崩れた日本語（例: 「再起動したけど直らん」「やり方を詳しく教えて」「サポートの人に代わって」）
から、最も意図が合致する続けて質問候補を特定します。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# 意図別の正規表現パターン
INTENT_PATTERNS: dict[str, list[str]] = {
    # 1. 再起動・未復旧・エラー継続（否定表現・改善せず）
    "unresolved": [
        r"(直ら|治ら|消え|動か|上がら|減ら)(ない|ん|ず|ません)",
        r"(点灯|点滅|点か|つか|付か|復旧|改善|回復|解消|解決)(し|せ)?(ない|ん|ず|ません)",
        r"(ダメ|だめ|駄目|動かない|動かん|消えん|直らん|治らん|つかない|つかん)",
        r"(消灯|赤点滅|赤点灯|消えたまま|消えてる|エラーのまま|落ちたまま|100%に再上昇|再上昇|再発|再失敗|再度エラー)",
        r"(また|再度|再び).*(エラー|失敗|ダメ|だめ|落ち)",
        r"(エラー|失敗)(した|になった|が出|で再失敗|です)",
    ],
    # 2. 詳細手順・操作画面・やり方の確認
    "detail_procedure": [
        r"(手順|やり方|方法|操作|画面|見方|コマンド|設定)",
        r"(詳しく|くわしく|具体的に|具体的な|詳細|どこを見れば|どうやって)",
        r"(教えて|知りたい|確認方法|確認手順)",
    ],
    # 3. エスカレーション・連絡・電話・手配
    "escalation": [
        r"(電話|連絡|サポート|デスク|オンコール|センター|窓口)",
        r"(代わって|代われ|人|オペレーター|人間)",
        r"(引き継ぎ|引継ぎ|エスカレーション|手配|交換|依頼)",
    ],
    # 4. 解決・完了・お礼
    "resolved": [
        r"(直った|治った|解決|いけた|復旧した|動いた|消えた)",
        r"(ありがとう|完了|おわり|終わりました|大丈夫)",
    ],
    # 5. 代替・迂回・ワークアラウンド
    "workaround": [
        r"(代替|代わり|lte|モバイルルーター|緊急用)",
        r"(lte|ルーター|緊急用|紙|手動|シール).*(切り替え|切替)",
        r"(一時保留|スキップ|先行|確定送信|次回バッチ)",
        r"(予備|移し替え|急ぎ|急いで|待たせ|別の方法|他の方法)",
    ],
}

# 特定ドメイン重要単語（加点用）
TOPIC_KEYWORDS = [
    "再起動", "再送", "ジョブ", "エラー", "バッチ", "poe", "スイッチ", "ap",
    "アクセスポイント", "wi-fi", "wifi", "lan", "led", "ランプ", "ルーター",
    "lte", "onu", "orderhub", "edi", "生鮮", "保留", "スキップ", "pod", "cpu", "sre", "保守"
]


def _get_ngrams(text: str, n: int = 2) -> set[str]:
    """文字列から文字単位の N-gram セットを抽出."""
    clean = re.sub(r"[^\w\s]", "", text.lower()).replace(" ", "")
    if len(clean) < n:
        return {clean} if clean else set()
    return {clean[i : i + n] for i in range(len(clean) - n + 1)}


def _calculate_ngram_similarity(s1: str, s2: str) -> float:
    """2つの文字列の 2-gram Jaccard 類似度を計算."""
    ngrams1 = _get_ngrams(s1, 2)
    ngrams2 = _get_ngrams(s2, 2)
    if not ngrams1 or not ngrams2:
        return 0.0
    intersection = len(ngrams1 & ngrams2)
    union = len(ngrams1 | ngrams2)
    return intersection / union if union > 0 else 0.0


def _detect_intents(text: str) -> set[str]:
    """テキストから合致するインテントを検出."""
    matched = set()
    for intent, patterns in INTENT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                matched.add(intent)
                break
    return matched


# 音声認識で混入しやすい言いよどみ（フィラー）
_FILLER_PATTERN = re.compile(r"(えーっと|えーと|えっと|えー|あのー|あの|うーん|うん|まあ|ええと|そのー)")
# 部分一致とみなす最小共通文字数
_MIN_PARTIAL_LEN = 4
# 1位と2位の差がこれ未満で、かつ1位が強一致でない場合は曖昧として割り当てない
_AMBIGUOUS_MARGIN = 0.08
_STRONG_SCORE = 0.9
# 詳細要求の意図のみで割り当てる際に必要な内容語の重なり（N-gram 類似 + 部分一致 + トピック語数）
_MIN_CONTENT_OVERLAP = 0.15
# 詳細要求の汎用語・助詞（これらを除いた残りを「発話固有の内容語」とみなす）
_GENERIC_DETAIL_WORDS = re.compile(
    r"(詳しく|くわしく|具体的に|具体的な|詳細|教えて|ください|知りたい|注意点|手順|やり方|方法|操作|画面|見方|"
    r"確認|どこを見れば|どうやって|いいの|いい|ですか|とき|時|もう少し|ちょっと|を|の|は|が|に|で|て|か)"
)


def normalize_voice_text(text: str) -> str:
    """フィラー・句読点・空白を除去して照合用に正規化する."""
    t = _FILLER_PATTERN.sub("", text)
    t = re.sub(r"[\s、。，．,.！!？?「」『』（）()・…ー〜~]+", "", t)
    return t.lower()


def _partial_containment(s1: str, s2: str) -> float:
    """最長共通部分文字列長 / 短い方の長さ (途中で途切れた発話・言いよどみ対策)."""
    if not s1 or not s2:
        return 0.0
    m = SequenceMatcher(None, s1, s2, autojunk=False).find_longest_match(0, len(s1), 0, len(s2))
    if m.size < _MIN_PARTIAL_LEN:
        return 0.0
    return m.size / min(len(s1), len(s2))


def _score_candidate(v_clean: str, v_norm: str, voice_intents: set[str], candidate: str) -> tuple[float, set[str]]:
    c_clean = candidate.strip()
    c_norm = normalize_voice_text(c_clean)
    # 1. 文字列 N-gram 類似度
    base_sim = _calculate_ngram_similarity(v_norm, c_norm)

    # 2. 候補側のインテント検出
    cand_intents = _detect_intents(c_clean)

    # 3. インテント一致ボーナス
    intent_match = voice_intents & cand_intents
    intent_bonus = 0.0
    if intent_match:
        intent_bonus = 0.50 * len(intent_match)
    elif voice_intents and cand_intents:
        # 互いに別の主要インテントを持っている場合はペナルティ
        intent_bonus = -0.20

    # 4. トピック単語の一致ボーナス
    topic_bonus = 0.0
    for kw in TOPIC_KEYWORDS:
        if kw in v_clean.lower() and kw in c_clean.lower():
            topic_bonus += 0.10
    topic_bonus = min(0.30, topic_bonus)

    # 5. 部分一致ボーナス（途中で途切れた発話・候補の一部を読み上げた発話）
    partial_bonus = 0.6 * _partial_containment(v_norm, c_norm)

    return base_sim + intent_bonus + topic_bonus + partial_bonus, intent_match


def match_followup_question(
    voice_text: str,
    candidates: list[str],
    threshold: float = 0.25,
) -> dict[str, Any]:
    """音声入力テキストに最も近い続けて質問候補をマッチング.

    Args:
        voice_text: 音声認識されたテキスト
        candidates: 提示されている続けて質問候補のリスト
        threshold: マッチとみなす最小スコア (0.0〜1.0)

    Returns:
        dict:
            matched: 最も近い候補（マッチなしの場合は None）
            score: 類似度スコア
            reason: 判定理由・マッチしたインテント
            assigned: True if matched else False
            runner_up: 2番目に近い候補
            margin: 1位と2位のスコア差
    """
    v_clean = voice_text.strip()
    v_norm = normalize_voice_text(v_clean)
    if not v_norm or not candidates:
        return {"matched": None, "score": 0.0, "reason": "empty_input", "assigned": False,
                "runner_up": None, "margin": 0.0}

    # 候補と完全一致（正規化後）は即確定
    for candidate in candidates:
        if normalize_voice_text(candidate) == v_norm:
            return {"matched": candidate, "score": 1.0, "reason": "exact_matched", "assigned": True,
                    "runner_up": None, "margin": 1.0}

    voice_intents = _detect_intents(v_clean)

    scored: list[tuple[float, str, set[str]]] = []
    for candidate in candidates:
        score, intent_match = _score_candidate(v_clean, v_norm, voice_intents, candidate)
        scored.append((score, candidate, intent_match))
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best_candidate, best_intent_match = scored[0]
    runner_up = scored[1][1] if len(scored) > 1 else None
    margin = best_score - scored[1][0] if len(scored) > 1 else best_score
    best_intent = list(best_intent_match)[0] if best_intent_match else None

    base = {"score": round(best_score, 3), "runner_up": runner_up, "margin": round(margin, 3)}

    # 「詳しく」「手順」など汎用の詳細要求だけが一致し、発話固有の内容語が候補と重ならない場合は推測で割り当てない
    # (例: 「レンズを拭くときの注意点を詳しく」を「保守中にする手順は？」に割り当てない。
    #  「どこを見ればいいの？」のように発話側に固有の内容語がない場合は従来どおり割り当てる)
    if best_intent_match == {"detail_procedure"}:
        b_norm = normalize_voice_text(best_candidate)
        residual = _GENERIC_DETAIL_WORDS.sub("", v_norm)
        content = (
            _calculate_ngram_similarity(v_norm, b_norm)
            + _partial_containment(v_norm, b_norm)
            + sum(1 for kw in TOPIC_KEYWORDS if kw in v_clean.lower() and kw in best_candidate.lower())
        )
        if len(residual) >= _MIN_PARTIAL_LEN and content < _MIN_CONTENT_OVERLAP:
            return {**base, "matched": None, "reason": "intent_only_generic", "assigned": False}

    if best_score >= threshold and best_score > 0:
        if margin < _AMBIGUOUS_MARGIN and best_score < _STRONG_SCORE:
            return {**base, "matched": None, "reason": "ambiguous", "assigned": False}
        return {
            **base,
            "matched": best_candidate,
            "reason": f"intent_matched:{best_intent}" if best_intent else "similarity_matched",
            "assigned": True,
        }

    return {**base, "matched": None, "reason": "below_threshold", "assigned": False}
