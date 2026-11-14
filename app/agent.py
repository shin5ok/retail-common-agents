"""Layer 2: ADK 2.x AIOps オーケストレーター & 4専門エージェント.

SPEC.md の機能要件 3.2, 3.3, 5 に基づき、以下のエージェント階層を定義:
- root_agent: aiops_orchestrator (ハブ機能・意図判定・ルーティング・回答統合・エスカレーション判定)
- sub_agents:
    - infra_specialist (AGENT 01: クラウド・サーバー基盤の障害、リソース逼迫の一次切り分け)
    - order_specialist (AGENT 02: サプライチェーン・受発注データの連携エラーや不整合の調査)
    - network_specialist (AGENT 03: 店舗WAN/LAN、機器接続の疎通不良の切り分けと復旧手順)
    - pos_specialist (AGENT 04: レジ端末や専用ゲート、電子棚札などのスマートストア機器トラブル原因追跡)

モデル: gemini-3.8-flash (thinking_config=ThinkingConfig(thinking_level="medium"))
Live音声対話時: gemini-3.8-live
"""

from __future__ import annotations

import os
import re
from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.followup_answers import CLOSE_FOLLOWUP, build_close_answer, get_specific_followup_answer
from app.rag_store import (
    MOCK_TICKETS,
    classify_domain,
    get_runbook,
    search_logs,
    search_tickets,
)
from app.tools import (
    evaluate_escalation,
    get_recovery_runbook,
    search_incident_tickets,
    search_splunk_logs,
)

# 2026年9月 GA モデル仕様: gemini-3.8-flash (テキスト・マルチモーダル通常対話用)
MODEL_NAME = os.getenv("AIOPS_MODEL", "gemini-3.8-flash")

# Gemini Live API (リアルタイム双方向音声ストリーミング) 設定
# Vertex AI (Agent Platform) 公式サポート: gemini-3.8-live (GA・推奨) / gemini-live-2.5-flash-native-audio (GA)
# 提供リージョン: us-central1 またはマルチリージョン us / eu (global は非対応)
# ※ gemini-3.1-flash-live-preview は Gemini Developer API 専用で Vertex AI では利用不可
LIVE_MODEL_NAME = os.getenv("LIVE_API_MODEL", "gemini-3.8-live")
LIVE_FALLBACK_MODEL_NAME = os.getenv("LIVE_API_FALLBACK_MODEL", "gemini-live-2.5-flash-native-audio")
LIVE_API_LOCATION = os.getenv("LIVE_API_LOCATION", "us-central1")


def _is_vertex_ai() -> bool:
    """Vertex AI バックエンドを使用する設定かどうかを判定する."""
    return os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1")


def live_client_kwargs() -> dict[str, Any] | None:
    """Live API 用 GenAI クライアントのリージョン指定 (Vertex AI 時のみ)."""
    if not _is_vertex_ai():
        return None
    return {
        "vertexai": True,
        "project": os.getenv("GOOGLE_CLOUD_PROJECT"),
        "location": LIVE_API_LOCATION,
    }

# gemini-3.8-flash では temperature/top_p ではなく thinking_level を使用
GENERATE_CONFIG = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_level="medium"),
    max_output_tokens=2048,
)

COMMON_TOOLS = [
    search_incident_tickets,
    search_splunk_logs,
    get_recovery_runbook,
    evaluate_escalation,
]

# =====================================================================
# 1. 専門エージェント定義 (Specialized Agents)
# =====================================================================

infra_specialist = Agent(
    name="infra_specialist",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    generate_content_config=GENERATE_CONFIG,
    description="クラウド／サーバー基盤、リソース逼迫・障害の一次切り分けを行う専門エージェント (AGENT 01)",
    instruction="""あなたは「インフラ スペシャリスト (AGENT 01)」です。
クラウド／サーバー基盤、APサーバー（ap-inv-prod）、Compute Engine、Cloud SQL、GKEのリソース逼迫やCPU 100%障害を担当します。

【行動指針】
1. ユーザーの問い合わせ内容から `search_splunk_logs` と `search_incident_tickets` (domain='infra') を実行し、直近のエラーログや類似チケットを抽出してください。
2. `get_recovery_runbook` (domain='infra') を取得し、一次切り分け手順（ワーカーPodのローリング再起動など）を確認してください。
3. `evaluate_escalation` を呼び出し、未解決時の有人SREオンコールチームへのエスカレーション窓口を整理してください。
4. 抽出した事実（過去チケット#10892等）に基づき、具体的かつ簡潔な一次切り分け手順を回答してください。
""",
    tools=COMMON_TOOLS,
)

order_specialist = Agent(
    name="order_specialist",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    generate_content_config=GENERATE_CONFIG,
    description="サプライチェーン／受発注、連携エラー・データ不整合の調査を行う専門エージェント (AGENT 02)",
    instruction="""あなたは「発注管理 スペシャリスト (AGENT 02)」です。
サプライチェーン／受発注、OrderHub、生鮮品（青果・鮮魚・精肉）の自動発注データ、基幹EDI連携バッチの未反映やエラーを担当します。

【行動指針】
1. `search_splunk_logs` と `search_incident_tickets` (domain='order') を実行し、EDI連携バッチのエラーログ（EDI-ERR-302など）や類似チケット（#11403など）を抽出してください。
2. `get_recovery_runbook` (domain='order') で差分再送ジョブ（job-order-resync-fresh）の手順を確認してください。
3. `evaluate_escalation` を呼び出し、発注締切時刻に間に合わない場合の受発注運用サポートデスクへのエスカレーションを確認してください。
4. 抽出した事実に基づき、一次対処手順を分かりやすく提示してください。
""",
    tools=COMMON_TOOLS,
)

network_specialist = Agent(
    name="network_specialist",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    generate_content_config=GENERATE_CONFIG,
    description="店舗WAN／LAN・機器接続、疎通不良の切り分けと復旧手順提示を行う専門エージェント (AGENT 03)",
    instruction="""あなたは「店舗ネットワーク スペシャリスト (AGENT 03)」です。
店舗WAN／LAN・機器接続、店舗バックヤードのネットワーク不通、ネットワークスイッチ（SW-BY-01）、Wi-Fiアクセスポイント、業務用タブレット・ハンディのオフライン障害を担当します。

【行動指針】
1. `search_splunk_logs` と `search_incident_tickets` (domain='network') を実行し、ネットワークスイッチの給電エラー（NET-SW-DOWN）や類似チケット（#11980など）を抽出してください。
2. `get_recovery_runbook` (domain='network') でバックヤードネットワークスイッチの電源再投入手順を確認してください。
3. `evaluate_escalation` を呼び出し、リンクアップしない場合の店舗ネットワーク保守センターへのエスカレーションを確認してください。
4. 抽出した事実に基づき、復旧手順をステップバイステップで提示してください。
""",
    tools=COMMON_TOOLS,
)

pos_specialist = Agent(
    name="pos_specialist",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    generate_content_config=GENERATE_CONFIG,
    description="レジ端末・売上データ連携、スマートストア機器エラーの原因追跡を行う専門エージェント (AGENT 04)",
    instruction="""あなたは「POS スペシャリスト (AGENT 04)」です。
レジ端末・売上データ連携、スマートカート（スマートショッピングカート）、スマートカート専用ゲート、電子棚札（ESL）、およびPOS端末のトラブルを担当します。

【デモ用サクセスシナリオ（ゴールデンパス）の対応】
1. ユーザーから「中央店のスマートカート専用ゲート（2番ゲート）を通過した際、ゲート通過認証が完了せず画面がフリーズしています。過去に似た事象はありますか？」などの問い合わせを受けた場合:
   - `search_splunk_logs` (query='中央店 スマートカート 2番ゲート タイムアウト', domain='pos') を呼び出し、直近のエラーログ（ERR-GATE-408 / BLE-SESS-FULL）を確認。
   - `search_incident_tickets` (query='中央店 スマートカート 専用ゲート 通過認証 フリーズ', domain='pos') を呼び出し、過去チケット `#12345` を抽出。
   - `get_recovery_runbook` (domain='pos') を呼び出し、ゲート脇制御ボックス内の無線通信レシーバー（GATE-AP-02）の再起動手順を確認。
   - `evaluate_escalation` (domain='pos', issue_summary='中央店 スマートカート2番ゲート通過認証タイムアウト', confidence_level='high') を呼び出す。
2. 回答は以下の構成で自然かつ分かりやすく返却してください:
   「スマートカート専用ゲートの通過認証エラーについて確認しました。過去の対応履歴（チケット#12345）によると、ゲート上部の無線通信レシーバー（GATE-AP-02）のセッション飽和による通信タイムアウトが原因の可能性があります。一次切り分けとして、当該ゲート脇制御ボックス内の通信レシーバーの再起動をお試しください。解決しない場合は有人サポートへエスカレーションしますか？」
""",
    tools=COMMON_TOOLS,
)


# =====================================================================
# 2. オーケストレーター定義 (Root Agent)
# =====================================================================

root_agent = Agent(
    name="aiops_orchestrator",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    generate_content_config=GENERATE_CONFIG,
    description="AIOps オーケストレーター: 問い合わせの意図判定、専門エージェントへのルーティング、回答統合、有人エスカレーション判定を行うハブエージェント",
    instruction="""あなたは「AIOps オーケストレーター」です。
運用担当者からの障害・仕様問い合わせを自然言語で受け付け、最適な専門エージェントへルーティングし、調査結果を統合して返答します。

【専門エージェント一覧】
1. infra_specialist (AGENT 01: インフラ スペシャリスト): クラウド／サーバー基盤、リソース逼迫・障害の一次切り分け
2. order_specialist (AGENT 02: 発注管理 スペシャリスト): サプライチェーン／受発注、連携エラー・データ不整合の調査
3. network_specialist (AGENT 03: 店舗ネットワーク スペシャリスト): 店舗WAN／LAN・機器接続、疎通不良の切り分けと復旧手順提示
4. pos_specialist (AGENT 04: POS スペシャリスト): レジ端末・売上データ連携、スマートストア機器エラーの原因追跡

【業務フロー】
1. ユーザーからの入力を解析し、何に関する問い合わせかを意図判定（Intent Classification）します。
2. 判定結果に基づき、最適な専門エージェント（infra_specialist, order_specialist, network_specialist, pos_specialist）へタスクをルーティングしてください。
3. 専門エージェントから返却された過去チケット（#12345等）やSplunkログ、復旧Runbook手順を統合（Aggregation）し、運用担当者が即座に行動できる簡潔・丁寧な日本語で回答してください。
4. 解決しない場合の有人サポートへのエスカレーション確認メッセージを添えてください。
""",
    sub_agents=[
        infra_specialist,
        order_specialist,
        network_specialist,
        pos_specialist,
    ],
    tools=COMMON_TOOLS,
)

# ADK 2.x App 定義 (ディレクトリ名 'app' と一致)
app = App(
    name="app",
    root_agent=root_agent,
)

# Gemini Live API (リアルタイム双方向音声ストリーミング) 専用エージェント & App
# Live API サポートモデル (gemini-3.8-live / gemini-live-2.5-flash-native-audio) を使用
LIVE_INSTRUCTION = """あなたは店舗システム運用の「AIアシスタント」です。
現場担当者とリアルタイム音声で双方向に対話し、インフラ・受発注・店舗ネットワーク・POSの障害や問い合わせを迅速に一次切り分けします。
音声対話に適した、簡潔・丁寧で分かりやすい日本語で回答してください。
"""


def build_live_app(model_name: str) -> App:
    """指定した Live API モデルで Live 専用 App を生成する.

    Vertex AI 利用時は Live API 提供リージョン (LIVE_API_LOCATION) を明示し、
    テキスト用モデルの GOOGLE_CLOUD_LOCATION (global 等) の影響を受けないようにする。
    """
    agent = Agent(
        name="live_agent",
        model=Gemini(
            model=model_name,
            retry_options=types.HttpRetryOptions(attempts=3),
            client_kwargs=live_client_kwargs(),
        ),
        description="Gemini Live API 双方向低遅延リアルタイム音声対話エージェント",
        instruction=LIVE_INSTRUCTION,
        tools=COMMON_TOOLS,
    )
    return App(name="live_app", root_agent=agent)


live_app = build_live_app(LIVE_MODEL_NAME)
live_agent = live_app.root_agent


# =====================================================================
# 3. オフライン / モック推論エンジン（堅牢性・デモ確実性担保）
# =====================================================================
# セッションごとの会話文脈（直前のドメイン・過去チケット・回答）を保持するインメモリメモリ
SESSION_CONTEXT: dict[str, dict[str, Any]] = {}
LATEST_SESSION_CONTEXT: dict[str, Any] | None = None

# 自然言語・音声口語表現による更問・意図分類キーワード
_RESOLVED_KEYWORDS = [
    "直った", "復旧した", "解決した", "動いた", "ありがとう", "助かった",
    "大丈夫", "いけた", "治った", "消えたよ", "できた", "直りました", "解決しました",
    "治りました", "消えました", "正常に戻", "正常になった", "正常です", "正常に動", "正常復旧",
    "動きました", "復旧しました", "サンキュー", "お疲れ様", "対応を完了",
]
_ESCALATION_KEYWORDS = [
    "連絡する", "連絡して", "引き継ぐ", "引き継ぎ", "エスカレーション", "呼んで",
    "電話する", "通報する", "保守窓口へ", "サポートデスクへ", "チームへ", "修理を依頼",
    "手配を要請", "出動を要請", "相談する", "代行依頼", "緊急エスカレーション",
    # 音声口語の補完
    "連絡したい", "連絡お願い", "連絡を", "電話して", "電話したい", "引き継いで", "修理を頼", "修理依頼",
    "手配を依頼", "手配して", "手配したい", "サポートに", "保守センターに", "窓口に",
]
_NOT_RESOLVED_KEYWORDS = [
    "直らない", "直らん", "治らん", "治らない", "あかん", "ダメ", "だめ", "無理",
    "エラー消えない", "まだ出てる", "まだ消えない", "まだフリーズ", "ランプ赤のまま",
    "赤点滅のまま", "点滅が止まらない", "復旧しない", "解決しない", "直りません",
    "反応しない", "再起動したけど", "再起動しても", "試したけど", "試しても", "やったけど", "やったんですが", "エラーのまま", "まだ壊れてる",
    "消えません", "消えない", "再上昇", "再失敗", "点灯しない", "つかない", "点かない",
    "画面真っ暗", "フリーズしたまま", "動いてない", "反応がない",
    # 音声口語・候補文面の補完
    "復帰しない", "戻らない", "変わりません", "変わらない", "点灯しません", "赤点灯のまま", "のままです",
]
_DETAIL_KEYWORDS = [
    "詳しく", "詳細", "どうやって", "やり方", "詳しい手順", "手順を詳しく", "操作手順", "画面のどこ", "どこ押す",
    "どこを操作", "操作方法", "注意点", "led", "ランプ", "どれくらい", "何分",
    "見方", "確認方法", "どこにある", "場所", "どのボタン",
    "どれ", "どの部分", "見分け方", "詳しく教えて", "やり方教えて", "どう見れば", "どこを見れば", "具体的に", "コマンド",
    # 音声口語・候補文面の補完
    "具体的な", "画面手順", "手順は", "方法は", "もう一度",
]
_WORKAROUND_KEYWORDS = [
    "代替", "別の方法", "他の方法", "紙", "手動", "交換", "予備", "移し替え", "紙の値札", "予備カート",
    "代用", "代わり", "別の手", "急ぎ", "急いで", "どうしよう", "待たせて", "pop", "シール", "先行送信", "ルーター", "一時切り替え",
    # 候補文面の補完（インフラ: DLQ 退避・夜間バッチ一時停止 / 受発注: 正常分の先行確定送信）
    "退避手順", "一時停止", "確定送信",
]
# 同じ回答をあえて繰り返してよい依頼（「もう一度」系）
_REPEAT_OK_KEYWORDS = ["もう一度", "もう1回", "もう一回", "繰り返", "再度教えて", "聞き逃"]


def detect_intent_flags(text: str) -> dict[str, bool]:
    """発話（または照合済みの続けて質問候補）から更問の意図フラグを判定する."""
    return {
        "resolved": any(k in text for k in _RESOLVED_KEYWORDS),
        "escalation": any(k in text for k in _ESCALATION_KEYWORDS),
        "not_resolved": any(k in text for k in _NOT_RESOLVED_KEYWORDS),
        "detail": any(k in text for k in _DETAIL_KEYWORDS),
        "workaround": any(k in text for k in _WORKAROUND_KEYWORDS),
    }


_ORDINAL_SUFFIX = r"(で|です|にして|を|の|のやつ|のやつで|でお願い|でお願いします|をお願い|をお願いします|で頼む|にします)?"
_ORDINAL_PATTERNS: list[tuple[int, str]] = [
    (0, r"^(1|１|一|いち)(番|ばん|つ目|個目|番目)?" + _ORDINAL_SUFFIX + "$"),
    (1, r"^(2|２|二|に)(番|ばん|つ目|個目|番目)" + _ORDINAL_SUFFIX + "$"),
    (2, r"^(3|３|三|さん)(番|ばん|つ目|個目|番目)" + _ORDINAL_SUFFIX + "$"),
    (0, r"^(最初|一番上|いちばん上|上)" + _ORDINAL_SUFFIX + "$"),
    (1, r"^(真ん中|まんなか|二番目|2番目)" + _ORDINAL_SUFFIX + "$"),
    (-1, r"^(最後|一番下|いちばん下|下)" + _ORDINAL_SUFFIX + "$"),
]


def select_followup_by_ordinal(text: str, candidates: list[str]) -> str | None:
    """「2番」「いちばん」「3つ目」「最初のやつ」「最後の」など番号・位置による候補選択を解釈する."""
    t = re.sub(r"[\s、。！!？?]", "", text)
    for idx, pat in _ORDINAL_PATTERNS:
        if candidates and idx < len(candidates) and re.match(pat, t):
            return candidates[idx]
    return None


# =====================================================================
# デモ向け: 口語の更問い（相づち・漠然とした質問）を聞き返さずに解釈する
# =====================================================================
_CONV_CONTACT = ["誰に", "だれに", "連絡先", "どこに連絡", "問い合わせ先", "窓口は", "内線"]
_CONV_CAUSE = ["原因", "なぜ", "なんで", "どうして", "理由"]
_CONV_HISTORY = ["過去", "事例", "前にも", "以前", "似た", "同じような"]
_CONV_STATUS = ["状況", "今どう", "いまどう", "進捗", "ステータス", "どうなって"]
_CONV_CUSTOMER = ["お客様", "お客さん", "説明", "案内文"]
_CONV_NO = ("いいえ", "いや", "結構", "やめて", "まだいい", "不要", "しなくて", "いらない", "ノー")
_CONV_YES = ("はい", "お願い", "よろしく", "そうして", "ええ", "うん", "頼む", "頼みます", "オッケー", "ok", "yes", "了解お願い")
_CONV_ACK = ["やってみ", "わかりました", "分かりました", "了解", "承知", "試してみ", "しました", "やりました", "実施", "確認します", "かしこまり", "やります"]


def classify_conversational_followup(text: str) -> str:
    """候補・意図キーワードに当てはまらない口語の更問いを分類する（デモで聞き返しを出さないため）.

    Returns:
        cause / history / status / customer / no / yes / ack / next のいずれか。
    """
    t = re.sub(r"[\s、。！!？?…]", "", text).lower()
    if any(k in t for k in _CONV_CONTACT):
        return "contact"
    if any(k in t for k in _CONV_CAUSE):
        return "cause"
    if any(k in t for k in _CONV_HISTORY):
        return "history"
    if any(k in t for k in _CONV_STATUS):
        return "status"
    if any(k in t for k in _CONV_CUSTOMER):
        return "customer"
    if t.startswith(_CONV_NO):
        return "no"
    if t.startswith(_CONV_YES):
        return "yes"
    if any(k in t for k in _CONV_ACK):
        return "ack"
    return "next"


def asks_escalation(answer: str) -> bool:
    """直前の回答が「エスカレーションしますか？」等の確認で終わっているか."""
    tail = answer[-120:]
    return ("エスカレーション" in tail or "連絡しますか" in tail) and ("ますか" in tail)


def with_lead(answer: str, lead: str) -> str:
    """回答の【対応指示】直後に一言添える（画面の解析で冒頭行が落ちないよう、見出しの内側に入れる）."""
    if "【対応指示】\n" in answer:
        return answer.replace("【対応指示】\n", f"【対応指示】\n{lead}\n", 1)
    return f"{lead}\n{answer}"


def build_conversational_answer(
    kind: str,
    agent_label: str,
    top_ticket: dict[str, Any] | None,
    tickets: list[dict[str, Any]],
    last_answer: str,
    escalation_team: str = "",
) -> tuple[str, str] | None:
    """相づち・漠然とした質問への会話的な回答を生成する. 該当しない種類は None（通常の回答を使う）."""
    t = top_ticket or {}
    tid = t.get("ticket_id", "")
    title = t.get("title", "")
    first_step = ""
    if "【対応指示】" in last_answer:
        body = last_answer.split("【対応指示】", 1)[1].split("【理由】", 1)[0].strip()
        lead_prefixes = ("状況を踏まえ", "改めてご案内", "ありがとうございます", "現在、", "（実施中", "現在の手順")
        steps = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith(lead_prefixes)]
        first_step = steps[0] if steps else ""

    if kind == "ack":
        answer = (
            "【対応指示】\n"
            "ありがとうございます。手順を実施したら、ランプや画面の状態がどう変わったか教えてください。\n"
            + (f"（実施中の手順: {first_step}）\n" if first_step else "")
            + "\n【理由】\n"
            "実施後の状態で、復旧完了か、次の切り分けやエスカレーションが必要かを判断するためです。"
        )
        technical = f"【対応状況】\n・担当: {agent_label}\n・参照チケット: {tid}\n・状態: 現場で手順実施中（結果待ち）"
        return answer, technical
    if kind == "cause":
        answer = (
            "【対応指示】\n"
            f"原因は次のとおりと推定しています。\n{t.get('root_cause') or title}\n\n"
            "【理由】\n"
            f"過去の類似チケット（{tid}）の調査結果と、今回の症状が一致しているためです。"
        )
        technical = f"【原因分析】\n・参照チケット: {tid} {title}\n・推定原因: {t.get('root_cause', '-')}"
        return answer, technical
    if kind == "history":
        lines = [
            f"・{x.get('ticket_id')} {x.get('title')}（解決まで約{x.get('resolved_in_minutes', '-')}分）"
            for x in tickets[:2]
        ]
        answer = (
            "【対応指示】\n"
            "過去に似た事例があります。\n" + "\n".join(lines) + "\n"
            f"前回は「{t.get('resolution') or '同様の一次対応'}」で復旧しています。\n\n"
            "【理由】\n"
            "同じ原因で発生した過去事例の解決方法を使うと、最短で復旧できるためです。"
        )
        technical = "【類似事例】\n" + "\n".join(lines)
        return answer, technical
    if kind == "status":
        answer = (
            "【対応指示】\n"
            f"現在、{agent_label}が対応中です（参照チケット: {tid}）。\n"
            + (f"現在の手順: {first_step}\n" if first_step else "")
            + "手順の結果を教えていただければ、次の対応に進みます。\n\n"
            "【理由】\n"
            "過去事例をもとに一次切り分けを進めており、現場の結果で次の手順が決まるためです。"
        )
        technical = f"【対応状況】\n・担当: {agent_label}\n・参照チケット: {tid} {title}"
        return answer, technical
    if kind == "customer":
        answer = (
            "【対応指示】\n"
            "お客様には次のようにご案内してください。\n"
            "「ただいま機器の不具合により、ご迷惑をおかけしております。すぐに復旧作業を行っておりますので、少々お待ちください。」\n\n"
            "【理由】\n"
            "原因や専門用語を伝えるより、復旧中であることと待ち時間の目安を伝える方が、お客様の不安を減らせるためです。"
        )
        technical = f"【お客様対応】\n・参照チケット: {tid}\n・案内方針: 謝罪＋復旧中の明示＋待機のお願い"
        return answer, technical
    if kind == "contact":
        answer = (
            "【対応指示】\n"
            f"連絡先は「{escalation_team or '有人サポートデスク'}」です。\n"
            "一次対応で復旧しない場合は、チケット番号と実施済みの手順を伝えて連絡してください。\n\n"
            "【理由】\n"
            "この障害の専門窓口で、過去事例の経緯を踏まえて最短で引き継げるためです。\n\n"
            "このままエスカレーションしますか？"
        )
        technical = f"【エスカレーション先】\n・窓口: {escalation_team}\n・参照チケット: {tid}"
        return answer, technical
    return None


def build_clarification(query: str, options: list[str], reason: str) -> tuple[str, str]:
    """推測で回答せず、直前の候補を提示して意図を確認する応答を生成する."""
    lead = (
        "先ほどと同じご案内になるため、確認させてください。"
        if reason == "repeat"
        else f"「{query}」のご質問の意図を特定できませんでした。"
    )
    lines = [f"{i}. {opt}" for i, opt in enumerate(options, start=1)]
    answer = (
        "【確認】\n"
        f"{lead}\n"
        "次のうち、どれに近いですか？\n"
        + "\n".join(lines)
        + "\n\n番号か、近い言葉でお答えください。"
    )
    technical = (
        "【聞き返し】\n"
        f"・理由: {'同一回答の繰り返し防止' if reason == 'repeat' else '意図不明（候補不一致・意図キーワードなし）'}\n"
        f"・入力: {query}"
    )
    return answer, technical


def run_aiops_pipeline(
    query: str,
    session_id: str = "default",
    image_data: str | None = None,
) -> dict[str, Any]:
    """オフライン環境・APIキー未設定時でも 100% 確実に動作する推論パイプライン.

    SPEC.md のゴールデンパスおよび実店舗設備シナリオにおいて、カメラ画像入力、
    マイク音声入力や自然言語・口頭表現に対しても前回の文脈を確実に引き継いで応答します。
    """
    global LATEST_SESSION_CONTEXT
    if (session_id == "default" or session_id.startswith("voice-session-new")) and LATEST_SESSION_CONTEXT:
        prev_ctx = LATEST_SESSION_CONTEXT
    else:
        prev_ctx = SESSION_CONTEXT.get(session_id)
    query_lower = query.lower()
    has_image = bool(image_data)
    is_visual_query = False

    # 自然言語・音声口語表現による更問・意図分類キーワード
    flags = detect_intent_flags(query)
    is_resolved = flags["resolved"]
    is_escalation_request = flags["escalation"]
    is_not_resolved = flags["not_resolved"]
    is_detail_request = flags["detail"]
    is_workaround = flags["workaround"]

    # 直前のやり取りがない初回問い合わせの場合、症状説明での更問フラグ誤判定を防止
    if not prev_ctx:
        has_explicit_continuation = any(k in query for k in [
            "再起動したけど", "再起動しても", "試したけど", "試しても", "やったけど", "やったんですが",
            "引き継ぐ", "引き継ぎ", "エスカレーション", "修理を依頼", "出動を要請"
        ])
        if not has_explicit_continuation:
            is_resolved = False
            is_not_resolved = False
            is_detail_request = False
            is_workaround = False
            is_escalation_request = False

    # 画像が添付されている場合は初期の画像ビジュアル診断を最優先
    if has_image:
        is_resolved = False
        is_escalation_request = False
        is_not_resolved = False
        is_detail_request = False
        is_workaround = False

    from app.okf_manager import get_okf_manager

    okf_mgr = get_okf_manager()
    matched_uc, okf_score = okf_mgr.match_use_case(query)

    is_infra_query = any(k in query_lower for k in ["cpu", "サーバー", "サーバ", "ap-inv", "504", "gateway timeout", "inf-cpu", "pod", "ポッド"])
    # 写真アップロード時は本文に関係なく POS のカメラ画像診断 (ERR-GATE-408) に固定する
    is_visual_query = has_image or (not is_infra_query and (
        any(k in query for k in ["写真", "撮影", "写した", "撮った", "ビジュアル", "これ見て", "ランプ見て", "画像診断", "カメラで撮", "カメラ撮影"])
    ))
    if has_image or (is_visual_query and ("ランプ" in query or "画面" in query or "これ" in query or "写真" in query or "撮った" in query or "画像" in query)):
        cam_uc = okf_mgr.get_use_case("scenario-camera-diagnostic")
        if cam_uc:
            matched_uc = cam_uc
            okf_score = 0.98

    classification = classify_domain(query)

    # ユーザーが音声等で明示的に言及した特定設備（別ドメインへの切り替え用）
    explicit_domain = None
    if any(k in query_lower for k in ["cpu", "メモリ", "サーバー", "サーバ", "高負荷", "ap-inv", "504", "gateway timeout", "inf-cpu-100"]):
        explicit_domain = "infra"
    elif any(k in query_lower for k in ["発注", "受発注", "edi", "生鮮", "青果", "鮮魚", "精肉", "野菜", "魚", "肉", "edi-err-302", "orderhub"]):
        explicit_domain = "order"
    elif any(k in query_lower for k in ["バックヤード", "wifi", "wi-fi", "poe", "アクセスポイント", "ハンディ", "ネットワーク", "net-poe-down", "net-sw-down"]):
        explicit_domain = "network"
    elif any(k in query_lower for k in ["ゲート", "専用ゲート", "2番ゲート", "err-gate-408"]):
        explicit_domain = "pos"
    elif matched_uc and okf_score >= 0.65:
        explicit_domain = matched_uc.domain
    elif any(k in query_lower for k in ["電子棚札", "棚札", "値札", "電子値札", "esl", "基地局", "惣菜", "お惣菜", "値下げ", "値引き", "半額"]):
        explicit_domain = "pos"
    elif any(k in query_lower for k in ["カート", "smart cart", "重量検知", "カゴ", "スキャン漏れ", "err-cart-wgt-105"]):
        explicit_domain = "pos"

    # 他ドメインへの明示的シグナル（前回のドメインを引き継がず、適切な別エージェントに切り替える）
    has_other_domain_strong = False
    if prev_ctx:
        prev_dom = prev_ctx.get("domain")
        if matched_uc and okf_score >= 0.65 and matched_uc.domain != prev_dom:
            has_other_domain_strong = True
        elif explicit_domain and explicit_domain != prev_dom:
            has_other_domain_strong = True
        elif prev_dom != "network" and any(k in query_lower for k in ["wifi", "wi-fi", "poe", "アクセスポイント", "sw-by", "バックヤード", "ハンディ", "ネットワーク", "ネットがつながら", "有線lan", "無線lan"]):
            has_other_domain_strong = True
        elif prev_dom != "order" and any(k in query_lower for k in ["受発注", "生鮮品", "青果", "鮮魚", "精肉", "edi-err", "orderhub", "自動発注"]):
            has_other_domain_strong = True
        elif prev_dom != "infra" and any(k in query_lower for k in ["ap-inv", "cpu使用率", "inf-cpu", "gateway timeout", "cpu", "504", "サーバー", "サーバ"]):
            has_other_domain_strong = True
        elif prev_dom != "pos" and any(k in query_lower for k in ["スマートカート", "専用ゲート", "電子棚札", "err-gate-408", "2番ゲート"]):
            has_other_domain_strong = True

    # 直前の会話コンテキストと提示された更問候補の取得
    prev_followups = prev_ctx.get("suggested_followups", []) if prev_ctx else []
    is_suggested_match = False
    matched_followup: str | None = None
    match_info: dict[str, Any] = {}
    if prev_followups:
        clean_q = query.strip()
        ordinal_pick = None if has_other_domain_strong else select_followup_by_ordinal(clean_q, prev_followups)
        if ordinal_pick:
            is_suggested_match = True
            matched_followup = ordinal_pick
            match_info = {"score": 1.0, "reason": "ordinal_selected"}
        for f_cand in ([] if ordinal_pick else prev_followups):
            if clean_q == f_cand.strip() or clean_q in f_cand or f_cand in clean_q:
                is_suggested_match = True
                matched_followup = f_cand
                match_info = {"score": 1.0, "reason": "substring_matched"}
                break
        # 短文・口語表現のみサジェスト候補との照合を許可（長文の新規障害相談は誤照合させない）
        if not is_suggested_match and len(clean_q) <= 60:
            from app.followup_matcher import match_followup_question

            match_res = match_followup_question(clean_q, prev_followups, threshold=0.40)
            match_info = match_res
            if match_res.get("assigned"):
                is_suggested_match = True
                matched_followup = match_res["matched"]
        # 他ドメインの語（例: 鮮魚）を含んでも、候補と強く一致する発話は候補の意図を優先して担当を切り替えない
        if has_other_domain_strong:
            if matched_followup and float(match_info.get("score") or 0.0) >= 0.6:
                has_other_domain_strong = False
            else:
                is_suggested_match = False
                matched_followup = None
                match_info = {}

    # 明示的な別トピック・別ドメインへの新規問い合わせ宣言
    is_explicit_topic_switch = any(k in query for k in [
        "別の質問", "別の件", "別のトラブル", "別の障害", "次の質問", "話は変わ", "別件", "新しく", "新規の相談"
    ])

    # 新規シナリオ投入（まったく別の店舗・設備の詳細障害起票文）の判定
    # 同一ドメインでも OKF で別ユースケース（別チケット）と特定された詳細起票文は新規インシデントとして扱う
    prev_ticket_id = ((prev_ctx or {}).get("top_ticket") or {}).get("ticket_id")
    is_new_full_incident = (
        prev_ctx
        and matched_uc
        and okf_score >= 0.70
        and (matched_uc.domain != prev_ctx.get("domain") or matched_uc.ticket_id != prev_ticket_id)
        and len(query) > 60
        and any(k in query for k in ["発生", "店舗", "店の", "店で", "エラーコード", "検知"])
    )

    # OKF によるケース再特定（同一ドメイン内のケース切り替え・同一ケースの言い直し）:
    # 自然言語でワンクリックシナリオ相当の相談をされた場合、前回の担当の更問いとして抱え込まず、
    # OKF が特定したケースの初回対応から始める
    is_okf_case_switch = False
    if prev_ctx and matched_uc and okf_score >= 0.70 and not has_image:
        # 候補との完全一致・番号選択（score 1.0）や OKF に迫る強い一致のみ更問いを優先する
        # （「エラー」「画面」等の汎用語による候補のあいまい一致で、別ケースの相談を抱え込まない）
        followup_score = float(match_info.get("score") or 0.0)
        followup_strong = bool(matched_followup) and followup_score >= max(0.6, okf_score - 0.05)
        has_continuation_intent = any(
            [is_resolved, is_escalation_request, is_not_resolved, is_detail_request, is_workaround]
        )
        if matched_uc.ticket_id != prev_ticket_id:
            # ① 前回と別チケット: 更問い候補に強く一致していなければ新規案件
            is_okf_case_switch = not followup_strong
        else:
            # ② 前回と同じチケット: 候補一致も継続意図もなければ、聞き返さず初回手順を再提示
            is_okf_case_switch = not matched_followup and not has_continuation_intent
    if is_okf_case_switch:
        # 新規案件として扱うため、症状説明に含まれる語での更問フラグ誤判定を防止（初回問い合わせと同じ扱い）
        if not any(k in query for k in [
            "再起動したけど", "再起動しても", "試したけど", "試しても", "やったけど", "やったんですが",
            "引き継ぐ", "引き継ぎ", "エスカレーション", "修理を依頼", "出動を要請"
        ]):
            is_resolved = False
            is_not_resolved = False
            is_detail_request = False
            is_workaround = False
            is_escalation_request = False
        matched_followup = None
        is_suggested_match = False
        match_info = {}

    # 更問い（Follow-up）判定:
    # 新規別インシデント全文起票や明示的トピック転換、他ドメインへの明示的シグナル、
    # OKF によるケース再特定がない限り、ルーティング不要で前回の担当専門AIとLive自然会話
    is_followup = False
    if (
        prev_ctx
        and not has_image
        and not has_other_domain_strong
        and not is_okf_case_switch
        and not is_explicit_topic_switch
        and not is_new_full_incident
    ):
        is_followup = True

    conversational_kind: str | None = None
    if is_followup and prev_ctx:
        domain = prev_ctx["domain"]
        agent_id = prev_ctx["agent_id"]
        agent_label = prev_ctx["agent_label"]
        top_ticket = prev_ctx["top_ticket"]
        tickets = prev_ctx.get("tickets", [])
        logs = prev_ctx.get("logs", [])
        runbook = prev_ctx.get("runbook", get_runbook(domain))
        top_log = logs[0] if logs else None
        matched_uc = None
        okf_score = 0.0

        # 続けて質問の候補に一致した場合は、候補文面で意図を判定し直す
        # (音声の言い換え・言いよどみ・途切れでキーワードが欠けても、選ばれた候補どおりに回答する)
        if matched_followup:
            cand_flags = detect_intent_flags(matched_followup)
            is_resolved = cand_flags["resolved"]
            is_escalation_request = cand_flags["escalation"]
            is_not_resolved = cand_flags["not_resolved"]
            is_detail_request = cand_flags["detail"]
            is_workaround = cand_flags["workaround"]
        elif not has_image and not any(
            [is_resolved, is_escalation_request, is_not_resolved, is_detail_request, is_workaround]
        ):
            # デモ向け: 候補にも意図キーワードにも当てはまらない口語の更問いは、聞き返さず会話の流れで解釈する
            conversational_kind = classify_conversational_followup(query)
            if conversational_kind == "yes" and asks_escalation(str(prev_ctx.get("last_answer", ""))):
                is_escalation_request = True
            elif conversational_kind == "no":
                is_workaround = True
            else:
                if conversational_kind == "yes":
                    conversational_kind = "ack"
                # 会話的な回答で置き換えない種類（next）は、現在の手順の詳細で答える
                is_detail_request = True
    else:
        if matched_uc and okf_score >= 0.65:
            domain = matched_uc.domain
            agent_id = matched_uc.agent_id
            agent_label = matched_uc.agent_label
        elif explicit_domain and classification["domain"] != explicit_domain:
            agent_map = {
                "pos": ("pos_specialist", "AGENT 04: POS スペシャリスト"),
                "infra": ("infra_specialist", "AGENT 01: インフラ スペシャリスト"),
                "order": ("order_specialist", "AGENT 02: 発注管理 スペシャリスト"),
                "network": ("network_specialist", "AGENT 03: 店舗ネットワーク スペシャリスト"),
            }
            domain = explicit_domain
            agent_id, agent_label = agent_map[domain]
        else:
            domain = classification["domain"]
            agent_id = classification["agent_id"]
            agent_label = classification["agent_label"]

        tickets = search_tickets(query, domain=domain, top_k=2)
        if matched_uc and okf_score >= 0.65:
            # OKFで特定されたチケットを先頭に昇格
            target_ticket = next((t for t in MOCK_TICKETS if t.ticket_id == matched_uc.ticket_id), None)
            if target_ticket:
                t_dict = target_ticket.to_dict()
                t_dict["similarity_score"] = max(okf_score, 0.95)
                tickets = [t_dict] + [t for t in tickets if t.get("ticket_id") != matched_uc.ticket_id]

        logs = search_logs(query, domain=domain, top_k=2)
        runbook = get_runbook(domain)
        top_ticket = tickets[0] if tickets else None
        top_log = logs[0] if logs else None

    # 推論プロセス（Chain of Thought）のステップ構築
    intent_detail = (
        f"直前セッションの文脈を引き継ぎ、更問として【{domain.upper()}】領域を継続対応。"
        if is_followup
        else (
            f"OKFナレッジ（Open Knowledge Format）と照合し、「{matched_uc.title}」と特定（確信度: {okf_score * 100:.0f}%）。専門領域【{domain.upper()}】にディスパッチ。"
            if (matched_uc and okf_score >= 0.65)
            else f"入力内容を解析し、「{classification['matched_terms']}」から専門領域【{domain.upper()}】と判定（確信度: {classification['confidence'] * 100:.0f}%）。"
        )
    )
    reasoning_steps: list[dict[str, Any]] = []
    current_step = 1

    if has_image or (matched_uc and matched_uc.id == "scenario-camera-diagnostic") or is_visual_query:
        reasoning_steps.append({
            "step_no": current_step,
            "type": "visual_multimodal_analysis",
            "agent": "pos_specialist",
            "title": "📷 カメラ画像AIマルチモーダル解析 (Visual Multimodal Inspection)",
            "detail": "現場カメラ撮影画像を解析。ゲート無線レシーバー（GATE-AP-02）の外観、ERRインジケーター赤点滅、および端末ERR-GATE-408コードを検出。",
            "evidence": [
                {
                    "detected_device": "専用ゲート無線通信レシーバー (GATE-AP-02)",
                    "led_status": "ERR赤点滅（通信バッファ詰まり）",
                    "screen_error": "ERR-GATE-408（通過認証タイムアウト）",
                    "has_image": has_image,
                }
            ],
        })
        current_step += 1

    reasoning_steps.extend([
        {
            "step_no": current_step,
            "type": "intent_classification",
            "agent": "aiops_orchestrator",
            "title": "ユーザー意図判定 (Intent Classification)",
            "detail": intent_detail,
            "evidence": [
                {
                    "domain": domain,
                    "matched_terms": classification["matched_terms"] if not is_followup else "文脈引き継ぎ（更問）",
                    "confidence": classification["confidence"] if not is_followup else 0.99,
                }
            ],
        },
        {
            "step_no": current_step + 1,
            "type": "agent_routing",
            "agent": "aiops_orchestrator",
            "title": f"専門エージェントへルーティング ➔ {agent_label}",
            "detail": f"タスクを専門領域エージェント `{agent_id}` に動的ディスパッチしました。",
            "evidence": [{"target_agent": agent_id, "label": agent_label}],
        },
        {
            "step_no": current_step + 2,
            "type": "rag_ticket_search",
            "agent": agent_id,
            "title": f"BigQuery ベクトル検索: 過去インシデント照合 ({top_ticket['ticket_id'] if top_ticket else 'なし'})",
            "detail": (
                f"過去チケット {top_ticket['ticket_id']}「{top_ticket['title']}」を抽出。"
                f"過去の根本原因: {top_ticket['root_cause']}"
                if top_ticket
                else "該当チケットなし"
            ),
            "evidence": tickets,
        },
        {
            "step_no": current_step + 3,
            "type": "rag_log_search",
            "agent": agent_id,
            "title": f"Splunk システムエラーログ抽出 ({top_log['error_code'] if top_log else 'なし'})",
            "detail": (
                f"ホスト `{top_log['host']}` より異常ログ検出: {top_log['message']}"
                if top_log
                else "直近エラーなし"
            ),
            "evidence": logs,
        },
        {
            "step_no": current_step + 4,
            "type": "runbook_lookup",
            "agent": agent_id,
            "title": f"標準復旧手順書 (Runbook: {runbook['runbook_id']}) の適用",
            "detail": f"『{runbook['title']}』の一次切り分け手順（全{len(runbook['steps'])}ステップ）を特定。",
            "evidence": [runbook],
        },
        {
            "step_no": current_step + 5,
            "type": "escalation_check",
            "agent": "aiops_orchestrator",
            "title": "エスカレーション判定 & 回答統合 (Aggregation)",
            "detail": f"一次切り分け手順を提示し、未解決時の有人サポート（{runbook['escalation_team']}）へのエスカレーション確認を付与。",
            "evidence": [
                {
                    "team": runbook["escalation_team"],
                    "criteria": runbook["escalation_criteria"],
                }
            ],
        },
    ])

    suggested_followups: list[str] = []
    technical_details: str = ""

    # 解決・感謝メッセージの場合のショートカット
    if is_resolved:
        answer_text = (
            "復旧のご報告ありがとうございます！無事にトラブルが復旧して安心いたしました。\n\n"
            "対応履歴はシステムカルテに正常完了として記録しました。他に気になる設備やご質問はございますか？"
        )
        technical_details = (
            "【インシデント対応完了】\n"
            f"・対象領域: {domain.upper()}\n"
            f"・担当エージェント: {agent_label}\n"
            "・ステータス: RESOLVED（正常復旧）\n"
            "・カルテ更新: 完了フラグ記録、チケット照合クローズ"
        )
        suggested_followups = [
            "他の売場・設備の稼働状態を確認する",
            "事例データベースを検索する",
            "対応を完了する",
        ]
        # 復旧報告のあとに「対応を完了する」を選んだ場合は、同じお礼文を繰り返さず締めの回答にする
        if (
            is_followup
            and prev_ctx
            and matched_followup == CLOSE_FOLLOWUP
            and str(prev_ctx.get("last_answer", "")).startswith("復旧のご報告")
        ):
            answer_text, technical_details, suggested_followups = build_close_answer()
        result = {
            "status": "success",
            "session_id": session_id,
            "query": query,
            "domain": domain,
            "assigned_agent": agent_id,
            "assigned_agent_label": agent_label,
            "is_followup": bool(is_followup),
            "answer": answer_text,
            "technical_details": technical_details,
            "reasoning_steps": reasoning_steps,
            "tickets": tickets,
            "logs": logs,
            "runbook": runbook,
            "escalation_team": runbook["escalation_team"],
            "prompt_to_user": "他にお困りごとはございますか？",
            "suggested_followups": suggested_followups,
        }
        ctx = {
            "domain": domain,
            "agent_id": agent_id,
            "agent_label": agent_label,
            "top_ticket": top_ticket,
            "tickets": tickets,
            "logs": logs,
            "runbook": runbook,
            "last_answer": answer_text,
            "technical_details": technical_details,
            "suggested_followups": suggested_followups,
        }
        SESSION_CONTEXT[session_id] = ctx
        LATEST_SESSION_CONTEXT = ctx
        return result

    if domain == "pos":
        top_tid = top_ticket["ticket_id"] if top_ticket else "#12110"
        if top_tid == "#12110":
            if is_escalation_request:
                answer_text = (
                    "【対応指示】\n"
                    "店舗ITインフラ保守窓口（内線: #5100）へ緊急出動および基地局交換の手配を要請しました。現場では緊急値引シール貼付による販売を継続してください。\n\n"
                    "【理由】\n"
                    "基地局本体のハードウェア故障の可能性が高く、現場の再起動では復旧できないため、専門技術員による現地機材交換を手配したためです。"
                )
                technical_details = (
                    "【エスカレーション完了】\n"
                    "・窓口: 店舗ITインフラ保守窓口（内線: #5100）\n"
                    "・手配内容: ESL-GW-DELI-01 代替機発送・保守員現地出動要請\n"
                    "・暫定ステータス: 手動シール運用中 / POS売価マスタ反映正常"
                )
                suggested_followups = [
                    "緊急用値引シールの貼付ルールを確認する",
                    "別の売場（鮮魚・精肉）の棚札状態を確認する",
                    "対応を完了する",
                ]
            elif is_not_resolved:
                answer_text = (
                    "【対応指示】\n"
                    "1. 惣菜の対象商品に「緊急用値引シール」を手動で貼り、販売を継続してください。\n"
                    "2. 店舗管理画面から該当棚札の「強制オフライン設定」を行ってください。\n\n"
                    "【理由】\n"
                    "再起動でも値引き表示が更新されないことを確認しました。基地局本体の故障またはアンテナ障害の可能性が高く、現場での再起動では復旧が困難なためです。\n\n"
                    "店舗ITインフラ保守窓口（内線: #5100）へ緊急エスカレーションします。"
                )
                technical_details = (
                    "【システム診断詳細】\n"
                    "・対象機器: ESL無線基地局 (ESL-GW-DELI-01)\n"
                    "・推定故障: RFフロントエンドモジュール破損、またはSub-GHzアンテナ給電不良\n"
                    "・エスカレーション先: 店舗ITインフラ保守窓口（内線: #5100）\n"
                    "・関連手順書: RB-POS-003（ESL基地局障害対応）"
                )
                suggested_followups = [
                    "店舗ITインフラ保守窓口へ引き継ぐ",
                    "緊急用値引シールの貼付ルールを確認する",
                    "別の売場（鮮魚・精肉）の棚札状態を確認する",
                ]
            elif is_workaround:
                answer_text = (
                    "【対応指示】\n"
                    "1. バックヤードのプリンターから「タイムセール用POP」を印刷し、棚にクリップ留めしてください。\n"
                    "2. レジで通常通り値引き価格で通ることを確認してください。\n\n"
                    "【理由】\n"
                    "電子棚札が復旧するまでの間、お客様への正しい価格案内と販売を途切れさせないためです。"
                )
                technical_details = (
                    "【暫定運用仕様】\n"
                    "・POSマスタ状態: 価格マスタ自体は配信完了済み（レジスキャン時は正常に値引き適用）\n"
                    "・棚札表示のみが未同期状態のため、紙POP掲示により売場表示を補完\n"
                    "・夜間閉店後に基地局本体の交換を実施"
                )
                suggested_followups = [
                    "POSレジ側の売価が正しいか確認する方法は？",
                    "店舗ITインフラ保守窓口へ引き継ぐ",
                    "直りました、ありがとうございます",
                ]
            elif is_detail_request:
                answer_text = (
                    "【対応指示】\n"
                    "基地局のLEDランプを確認してください。電源投入後、約2分でPWR・LAN・RFがすべて緑または青で点滅すれば正常です。\n\n"
                    "【理由】\n"
                    "ランプの色で電源・LAN通信・電波送出が正常に動作しているかを確認するためです。"
                )
                technical_details = (
                    "【ESL-GW-DELI-01 LEDインジケーター仕様】\n"
                    "1. PWR: 緑点灯＝給電正常 / 消灯＝電源供給異常\n"
                    "2. LAN: 緑点滅＝通信中 / 橙＝100Mbps制限中 / 消灯＝リンクダウン\n"
                    "3. RF: 青点滅＝正常データ送出中 / 赤点灯＝送信バッファ溢れ（再起動要）"
                )
                suggested_followups = [
                    "RFランプが赤点灯のまま復帰しない場合は？",
                    "紙の値札や手動値引きシールへの一時切り替え手順は？",
                    "店舗ITインフラ保守窓口へ連絡する",
                ]
            else:
                answer_text = (
                    "【対応指示】\n"
                    "1. 惣菜バックヤード壁面の通信盤内にあるESL基地局（白い四角い機器）のLANケーブルを一度抜き、10秒待って差し直してください。\n"
                    "2. 店舗タブレットの「電子棚札 価格一括再送」を押して、値札が変わるか確認してください。\n\n"
                    "【理由】\n"
                    "基地局の一時的な通信詰まりにより、夕方の値下げ指示が棚札に届いていない可能性が高いためです。\n\n"
                    "解決しない場合は有人サポートへエスカレーションしますか？"
                )
                technical_details = (
                    "【システム診断詳細】\n"
                    "・対象機器: ESL無線基地局 (ESL-GW-DELI-01 / 惣菜エリア担当)\n"
                    "・検出エラー: ESL-SYNC-501 (Price Broadcast Queue Stuck)\n"
                    "・システム原因: Sub-GHz送信キューがタイムアウトにより滞留\n"
                    "・参照チケット: #12110 / 過去平均復旧時間: 約15分"
                )
                suggested_followups = [
                    "再起動しても値引き表示が変わりません",
                    "ESL基地局の再起動後に確認するLEDランプ状態は？",
                    "紙の値札や手動値引きシールへの一時切り替え手順は？",
                ]
        elif top_tid == "#12085":
            if is_escalation_request:
                answer_text = (
                    "【対応指示】\n"
                    "スマートストア保守サポート（内線: #4300）へカート交換・センサー修理の要請を送信しました。該当カートを待機エリアへ移動させてください。\n\n"
                    "【理由】\n"
                    "底面ロードセルセンサーの物理的故障の可能性が高く、店舗での調整ができないため、専門技術員によるパーツ交換を手配したためです。"
                )
                technical_details = (
                    "【エスカレーション完了】\n"
                    "・連絡先: スマートストア保守サポート（内線: #4300）\n"
                    "・手配内容: SC-POS-V3 重量センサー交換パーツ手配、シリアル診断ログ共有\n"
                    "・措置: 端末上『保守中（利用不可）』ステータス設定済み"
                )
                suggested_followups = [
                    "アテンダント端末で『保守中』に切り替える方法は？",
                    "お客様の購入商品を予備カートへ移し替える手順は？",
                    "対応を完了する",
                ]
            elif is_not_resolved:
                answer_text = (
                    "【対応指示】\n"
                    "1. 該当カートの利用を直ちに中止し、予備カートをお客様へご案内してください。\n"
                    "2. アテンダント端末で該当カートを「保守中（利用停止）」に変更してください。\n\n"
                    "【理由】\n"
                    "ゼロ点リセット後もエラーが解消しないことを確認しました。底面の重量センサー破損や内部ケーブル断線の可能性が高く、現場調整では直らないためです。\n\n"
                    "スマートストア保守サポート（内線: #4300）へ修理を依頼します。"
                )
                technical_details = (
                    "【システム診断詳細】\n"
                    "・対象機器: スマートショッピングカート (SC-POS-V3)\n"
                    "・故障疑い: ロードセル歪みセンサーの物理的破損、またはA/D変換基板断線\n"
                    "・エスカレーション先: スマートストア保守サポート（内線: #4300）"
                )
                suggested_followups = [
                    "スマートストア保守サポートへ修理を依頼する",
                    "お客様の購入商品を予備カートへ移し替える手順は？",
                    "アテンダント端末で『保守中』に切り替える方法は？",
                ]
            elif is_workaround:
                answer_text = (
                    "【対応指示】\n"
                    "1. アテンダント端末で「カート乗換」を選び、予備カートのQRコードをスキャンしてください。\n"
                    "2. 商品を予備カートへ移し、お買物点数と金額が合っているかお客様と確認してください。\n\n"
                    "【理由】\n"
                    "すでにお客様がスキャンされた購入データをそのまま新しいカートへ安全に引き継ぎ、最初からやり直す負担をなくすためです。"
                )
                technical_details = (
                    "【カートセッション引き継ぎ仕様】\n"
                    "・Session Transfer API 経由で買物カゴUUIDを新端末へ再バインド\n"
                    "・登録済み商品リストおよび合計金額をそのまま承継"
                )
                suggested_followups = [
                    "該当カートを『保守中』にする手順は？",
                    "スマートストア保守サポートへ修理を依頼する",
                    "直りました、ありがとうございます",
                ]
            elif is_detail_request:
                answer_text = (
                    "【対応指示】\n"
                    "カゴ四隅のカメラレンズを乾いたメガネ拭き用クロス等で優しく拭いてください。アルコールは使わないでください。\n\n"
                    "【理由】\n"
                    "アルコールを使うとレンズのコーティングが剥がれ、認識率が落ちる原因になるためです。"
                )
                technical_details = (
                    "【センサー・カメラ清掃基準】\n"
                    "・カメラ仕様: 広角バーコード/商品認識カメラ（アルコール消毒液による表面コート白濁注意）\n"
                    "・点検箇所: カゴ底板と計量フレームの間にゴミ・レシート片の挟まりがないか目視確認"
                )
                suggested_followups = [
                    "ゼロ点リセットを行ってもエラーが消えません",
                    "お客様の購入商品を予備カートへ移し替える手順は？",
                    "スマートストア保守サポートへ修理を依頼する",
                ]
            else:
                answer_text = (
                    "【対応指示】\n"
                    "1. アテンダント用マスターバーコードをスキャンして保守メニューを開いてください。\n"
                    "2. 「重量センサーゼロ点リセット」を押し、カゴ上部のカメラレンズを布で軽く拭いてください。\n\n"
                    "【理由】\n"
                    "カゴの重量センサーのズレ、またはカメラの汚れにより誤検知が起きている可能性が高いためです。\n\n"
                    "解決しない場合はカートを交換の上、有人サポートへエスカレーションしますか？"
                )
                technical_details = (
                    "【システム診断詳細】\n"
                    "・対象機器: スマートカート (SC-POS-V3)\n"
                    "・検出エラー: ERR-CART-WGT-105 (Tare Calibration Drift)\n"
                    "・システム原因: ロードセル重量センサーの風袋ズレ（許容誤差±15g超過）または画像認識カメラ汚れ\n"
                    "・参照チケット: #12085 / 過去平均復旧時間: 約10分"
                )
                suggested_followups = [
                    "ゼロ点リセットを行ってもエラーが消えません",
                    "カート搭載カメラのレンズ汚れを拭く際の注意点は？",
                    "お客様の購入商品を予備カートへ移し替える手順は？",
                ]
        else:  # #12350 または 画像解析
            if is_escalation_request:
                answer_text = (
                    "【対応指示】\n"
                    "スマートストア有人サポートデスク（内線: #4300）へ緊急保守手配を要請しました。撮影画像ログおよび機器ステータスを転送済みです。\n\n"
                    "【理由】\n"
                    "無線レシーバーの内部基板ハードウェア故障が疑われ、現地での機材交換・配線確認が必要なためです。"
                )
                technical_details = (
                    "【エスカレーション完了】\n"
                    "・連絡先: スマートストア有人サポートデスク (内線: #4300)\n"
                    "・共有情報: カメラAI解析画像、GATE-AP-02 通信障害ログ\n"
                    "・緊急対応: ゲート一時閉鎖および隣接レーン誘導中"
                )
                suggested_followups = [
                    "隣接ゲートへの誘導手順を確認する",
                    "アテンダント端末でのゲート休止操作手順は？",
                    "対応を完了する",
                ]
            elif is_not_resolved:
                answer_text = (
                    "【対応指示】\n"
                    "当該ゲート（2番ゲート）のバーを下ろして休止中に設定し、隣接するゲートへお客様をご案内してください。あわせて店舗有人サポートデスクへエスカレーションします。\n\n"
                    "【理由】\n"
                    "無線レシーバーの再起動後もエラーランプが赤点滅のまま改善しないことを確認しました。通信基板または給電部の故障の可能性が高く、現場での即時復旧が難しいためです。\n\n"
                    "有人サポートデスク（内線: #4300）へ緊急保守手配を要請しますか？"
                )
                technical_details = (
                    "【システム診断詳細】\n"
                    "・対象機器: 店頭ゲート無線通信レシーバー (GATE-AP-02)\n"
                    "・診断状態: ERR赤点滅継続（内部バッファスタック再発）\n"
                    "・推定原因: 通信基板ハードウェア異常または電源供給ユニット故障\n"
                    "・連絡先: スマートストア有人サポートデスク (内線: #4300)"
                )
                suggested_followups = [
                    "スマートストア有人サポートデスクへ連絡する",
                    "隣接ゲートへの誘導手順を確認する",
                    "アテンダント端末でのゲート休止操作手順は？",
                ]
            elif is_workaround:
                answer_text = (
                    "【対応指示】\n"
                    "当該ゲートの休止コーンを設置し、隣接するゲートまたは有人案内レーンへお客様をご誘導ください。\n\n"
                    "【理由】\n"
                    "通信障害が発生しているゲートでのお客様の滞留を防ぎ、スムーズな退店をご案内するためです。"
                )
                technical_details = (
                    "【暫定運用仕様】\n"
                    "・対象レーン: 2番ゲート一時閉鎖\n"
                    "・迂回先: 1番ゲートまたはサービスカウンター案内\n"
                    "・アテンダント端末にてゲートステータス『休止中』に変更"
                )
                suggested_followups = [
                    "スマートストア有人サポートデスクへ連絡する",
                    "ゲート無線レシーバーの再起動手順をもう一度見る",
                    "直りました、ありがとうございます",
                ]
            elif is_detail_request:
                answer_text = (
                    "【対応指示】\n"
                    "レシーバー側面のランプを確認してください。「LINK」が緑点灯なら通信正常、「ERR」が赤点滅や赤点灯の場合は異常です。\n\n"
                    "【理由】\n"
                    "ランプの色と点灯状態で、通信回線が生きているか、または機器の再起動・交換が必要かを瞬時に判別するためです。"
                )
                technical_details = (
                    "【GATE-AP-02 LED仕様】\n"
                    "・PWR（電源）: 緑点灯＝給電正常 / 消灯＝電源断・ケーブル抜け\n"
                    "・LINK（通信）: 緑点灯＝正常リンク / 緑点滅＝送受信中 / 消灯＝回線断\n"
                    "・ERR（エラー）: 消灯＝正常 / 赤点滅＝通信バッファ詰まり（再起動要） / 赤点灯＝基板ハード故障\n"
                    "※電源OFF/ON後、約40秒でERR消灯・LINK緑点灯になれば復旧完了。"
                )
                suggested_followups = [
                    "再起動してもERRランプが赤点灯のままです",
                    "スマートストア有人サポートデスクへ連絡する",
                    "直りました、ありがとうございます",
                ]
            else:
                answer_text = (
                    "【カメラ画像AIビジュアル解析結果】\n"
                    "【対応指示】\n"
                    "ゲート横の制御ボックス内にある無線通信レシーバー（GATE-AP-02）の電源スイッチをOFFにし、10秒待ってからONにしてください。ランプが緑になったらカート画面の『再通信』を押してください。\n\n"
                    "【理由】\n"
                    "写真解析の結果、ゲート通信レシーバーが一時的に通信詰まりを起こしているため、電源の入れ直し（再起動）で通信を復旧させるためです。\n\n"
                    "再起動後も復旧しない場合は有人サポートへエスカレーションしますか？"
                )
                technical_details = (
                    "【📷 カメラ画像AIビジュアル解析結果】\n"
                    "・特定機器: 店頭ゲート無線通信レシーバー（型番: GATE-AP-02）\n"
                    "・検出状態: ERRランプ赤点滅（内部バッファスタック）\n"
                    "・端末画面エラー: 通信タイムアウト検知\n"
                    "・参照チケット: #12350 / 参照手順: RB-VISUAL-001"
                )
                suggested_followups = [
                    "再起動しても復旧しない",
                    "LEDランプの見方を詳しく教えて",
                    "スマートストア有人サポートデスクへ連絡する",
                ]
    elif domain == "infra":
        if is_escalation_request:
            answer_text = (
                "【対応指示】\n"
                "クラウドSREオンコールチーム（PagerDuty: #sre-oncall）へP1最優先インシデントとして招集を完了しました。現在エンジニアが接続調査を開始しています。\n\n"
                "【理由】\n"
                "ワーカーPodの再起動後もCPU負荷が解消せず、DBコネクション枯渇やデッドロックの可能性が高いため、インフラ専門チームによる緊急対処が必要なためです。"
            )
            technical_details = (
                "【SREエスカレーション完了】\n"
                "・招集先: PagerDuty #sre-oncall\n"
                "・対象サービス: InventorySyncWorker (GKE retail-prod)\n"
                "・共有メトリクス: CPU 100%, 504 Gateway Timeout レート急増"
            )
            suggested_followups = [
                "デッドレターキューの退避手順を教えて",
                "夜間バッチ処理を一時停止するコマンドを教えて",
                "対応を完了する",
            ]
        elif is_not_resolved:
            answer_text = (
                "【対応指示】\n"
                "対象ワーカーのオートスケーリング上限を一時的に引き上げ、SREオンコールチームを招集してください。\n\n"
                "【理由】\n"
                "再起動後もCPU負荷が下がりきらず、リソース枯渇による他サービスへの波及を防止するとともに、専門エンジニアによるメモリダンプ調査が必要なためです。"
            )
            technical_details = (
                "【システム診断詳細】\n"
                "・障害原因: デシリアライズ不備による永続パニックループまたはDBコネクションリーク\n"
                "・エスカレーション先: SREオンコールチーム (PagerDuty: #sre-oncall)\n"
                "・緊急措置: オートスケーリング上限引き上げ、メモリダンプ取得、トラフィック遮断"
            )
            suggested_followups = [
                "クラウドSREオンコールチームへ緊急エスカレーションする",
                "デッドレターキューに退避されたメッセージの再処理方法は？",
                "夜間バッチ処理を一時停止するコマンドを教えて",
            ]
        elif is_workaround:
            answer_text = (
                "【対応指示】\n"
                "在庫同期キューの滞留メッセージをデッドレターキュー（DLQ）へ一時退避させ、重い夜間バッチを一時停止してください。\n\n"
                "【理由】\n"
                "メインAPIサーバーへのリトライ負荷を遮断し、店舗側システムの応答速度を最優先で回復させるためです。"
            )
            technical_details = (
                "【暫定負荷退避コマンド】\n"
                "1. `gcloud pubsub subscriptions seek inventory-sync-sub --time=\"...\"`\n"
                "2. デッドレター退避によりCPU負荷を30%以下に低減"
            )
            suggested_followups = [
                "クラウドSREオンコールチームへ緊急エスカレーションする",
                "デッドレターキューに退避されたメッセージの再処理方法は？",
                "直りました、ありがとうございます",
            ]
        elif is_detail_request:
            answer_text = (
                "【対応指示】\n"
                "kubectl コマンドでワーカーのローリング再起動を実行し、PodがReadyになることを確認してください。\n\n"
                "【理由】\n"
                "プロセスを安全に順次再起動し、サービスの全面停止を伴わずに高負荷状態を解消するためです。"
            )
            technical_details = (
                "【詳細コマンド・手順】\n"
                "1. `kubectl rollout restart deployment/inventory-sync-worker -n retail-prod` を実行\n"
                "2. `kubectl get pods -l app=inventory-sync-worker -w` でReady状態（1/1）を確認\n"
                "3. Cloud MonitoringにてCPU使用率が80%未満に下降していることを確認"
            )
            suggested_followups = [
                "Podを再起動しても数分でCPUが100%に再上昇します",
                "デッドレターキューの退避手順を教えて",
                "クラウドSREオンコールチームへ連絡する",
            ]
        else:
            answer_text = (
                "【対応指示】\n"
                "在庫同期ワーカー（inventory-sync-worker）のPodをローリング再起動し、CPU使用率が低下するか確認してください。\n\n"
                "【理由】\n"
                "在庫同期処理の無限リトライによりCPU負荷が100%に達しているため、プロセスを再起動して滞留ジョブを解消するためです。\n\n"
                "再起動後もCPU負荷が低下しない場合は、クラウドSREオンコールチーム（PagerDuty: #sre-oncall）へエスカレーションしますか？"
            )
            technical_details = (
                "【システム診断詳細】\n"
                "・検出ログ: INF-CPU-100 (High CPU Utilization 100%)\n"
                "・対象コンポーネント: InventorySyncWorker\n"
                "・参照チケット: #10892 (類似度92%)\n"
                "・一次対応: ローリング再起動、デッドレターキュー退避確認"
            )
            suggested_followups = [
                "Podを再起動しても数分でCPUが100%に再上昇します",
                "Pod再起動の具体的なコマンド手順を教えて",
                "クラウドSREオンコールチームへ連絡する",
            ]
    elif domain == "order":
        if is_escalation_request:
            answer_text = (
                "【対応指示】\n"
                "受発注・EDI運用サポートデスク（内線: #3200）へ緊急要請を完了しました。バイヤーチームと連携し、午前9:00締切前の手動発注枠を確保します。\n\n"
                "【理由】\n"
                "新商品JANコードのエラーによりバッチ再送が通らないため、本部バイヤーによる緊急代理発注を実施するためです。"
            )
            technical_details = (
                "【エスカレーション完了】\n"
                "・連絡先: 受発注・EDI運用サポートデスク (内線: #3200)\n"
                "・対象カテゴリ: 生鮮品（青果・鮮魚・精肉）\n"
                "・共有エラー: EDI-ERR-302, 特売マスタJANコード不整合"
            )
            suggested_followups = [
                "エラー対象レコードを一時保留にする具体的な画面手順は？",
                "先行して正常分のみ確定送信する操作手順は？",
                "対応を完了する",
            ]
        elif is_not_resolved:
            answer_text = (
                "【対応指示】\n"
                "エラー対象レコードを一時保留にしたまま正常分のみを先行して確定送信し、未反映の生鮮品は受発注サポートデスクへ手動発注を依頼してください。\n\n"
                "【理由】\n"
                "発注締切時刻（午前9:00）に間に合わせるため、確定できる正常データを先に納品元へ送り、エラー分は手動でカバーするためです。"
            )
            technical_details = (
                "【システム診断詳細】\n"
                "・障害原因: 特売マスタ新商品JANコード基幹EDIテーブル未登録による整合性エラー\n"
                "・参照チケット: #11403\n"
                "・エスカレーション先: 受発注・EDI運用サポートデスク (内線: #3200)"
            )
            suggested_followups = [
                "受発注・EDI運用サポートデスクへ緊急エスカレーションする",
                "エラー対象レコードを一時保留にする具体的な画面手順は？",
                "先行して正常分のみ確定送信する操作手順は？",
            ]
        elif is_workaround:
            answer_text = (
                "【対応指示】\n"
                "正常な発注データのみを先行して確定送信し、エラー対象の生鮮品のみバイヤー直通ダイヤルにて手動発注を依頼してください。\n\n"
                "【理由】\n"
                "全体の納品遅延を回避しつつ、欠品リスクを最小限に抑えるためです。"
            )
            technical_details = (
                "【先行確定送信仕様】\n"
                "・OrderHubにて正常ステータスの128店舗分レコードのみコミット\n"
                "・エラーレコード#4412のみ次回バッチへ分離"
            )
            suggested_followups = [
                "受発注・EDI運用サポートデスクへ緊急エスカレーションする",
                "発注締切（9:00）を過ぎた場合の緊急手動発注方法は？",
                "直りました、ありがとうございます",
            ]
        elif is_detail_request:
            answer_text = (
                "【対応指示】\n"
                "OrderHub管理画面の『EDIエラー一覧』から、対象レコードにチェックを入れて『一時保留』を実行してください。\n\n"
                "【理由】\n"
                "エラーになっているレコードだけをスキップし、他の正常な発注データを滞りなく送信できるようにするためです。"
            )
            technical_details = (
                "【画面操作手順】\n"
                "1. OrderHub管理画面『連携ジョブ監視』>『EDIエラー一覧』を開く\n"
                "2. ステータス『ROLLBACK_FAILED』のレコードにチェック\n"
                "3. 右上『アクション』>『一時保留（次回バッチへ延期）』をクリックして確定"
            )
            suggested_followups = [
                "差分再送ジョブがエラー（EDI-ERR-302）で再失敗した場合は？",
                "先行して正常分のみ確定送信する操作手順は？",
                "受発注・EDI運用サポートデスクへ連絡する",
            ]
        else:
            answer_text = (
                "【対応指示】\n"
                "OrderHub管理画面でエラー対象のレコードを一時保留（スキップ）にし、差分再送ジョブを手動で再実行してください。\n\n"
                "【理由】\n"
                "新商品のコード不備で発注処理全体が中断しているため、エラー分を一時的に外して正常な発注を届けるためです。\n\n"
                "発注締切時刻（午前9:00）までに完了しない場合は、受発注・EDI運用サポートデスク（内線: #3200）へ緊急エスカレーションしますか？"
            )
            technical_details = (
                "【システム診断詳細】\n"
                "・検出ログ: EDI-ERR-302 (Master JAN Code Mismatch)\n"
                "・対象ジョブ: job-order-resync-fresh\n"
                "・参照チケット: #11403 (類似度88%)\n"
                "・対応締切: 午前09:00"
            )
            suggested_followups = [
                "差分再送ジョブがエラー（EDI-ERR-302）で再失敗した場合は？",
                "エラー対象レコードを一時保留（スキップ）する具体的な画面手順は？",
                "受発注・EDI運用サポートデスクへ連絡する",
            ]
    else:  # network
        if is_escalation_request:
            answer_text = (
                "【対応指示】\n"
                "店舗ネットワーク保守センター（内線: #5100）へ交換機材の手配と回線調査の要請を送信しました。予備スイッチの配送を手配しています。\n\n"
                "【理由】\n"
                "ネットワークスイッチ本体の電源部ハードウェア故障が疑われ、現地での機材交換が必要なためです。"
            )
            technical_details = (
                "【エスカレーション完了】\n"
                "・連絡先: 店舗ネットワーク保守センター (内線: #5100)\n"
                "・障害機器: Cisco Catalyst MS120-8LP (SW-BY-01)\n"
                "・暫定回線: 緊急用LTEルーター（STORE-EMERGENCY）稼働中"
            )
            suggested_followups = [
                "緊急用モバイルルーター（LTE）への切り替え手順を詳しく",
                "ONU（光回線装置）のアラームランプが赤点滅している時は？",
                "対応を完了する",
            ]
        elif is_not_resolved:
            answer_text = (
                "【対応指示】\n"
                "店長室の緊急用モバイルルーター（SIM内蔵）を起動し、店舗端末のWi-Fiを緊急用SSIDに切り替えて業務を継続してください。ネットワーク保守センターへ交換機材を手配します。\n\n"
                "【理由】\n"
                "電源の入れ直し後もWi-Fi機器が起動しないことを確認しました。スイッチ本体の電源故障または構内LAN断線の可能性が高く、店頭業務を止めないようバックアップ回線へ切り替えるためです。"
            )
            technical_details = (
                "【システム診断詳細】\n"
                "・障害原因: SW-BY-01 電源モジュール過電流故障または構内LAN物理断線\n"
                "・緊急SSID: STORE-EMERGENCY\n"
                "・連絡先: 店舗ネットワーク保守センター (内線: #5100)"
            )
            suggested_followups = [
                "店舗ネットワーク保守センターへ交換機材の手配を依頼する",
                "緊急用モバイルルーター（LTE）への切り替え手順を詳しく",
                "ONU（光回線装置）のアラームランプが赤点滅している時は？",
            ]
        elif is_workaround:
            answer_text = (
                "【対応指示】\n"
                "店長室の緊急用LTEモバイルルーターの電源をONにし、ハンディ・タブレットの接続先Wi-Fiを「STORE-EMERGENCY」に切り替えてください。\n\n"
                "【理由】\n"
                "バックヤードの有線LANが復旧するまでの間、入荷検品や品出し作業を止まらせないためです。"
            )
            technical_details = (
                "【代替Wi-Fi仕様】\n"
                "・SSID: STORE-EMERGENCY (LTE SIM内蔵ルーター)\n"
                "・認証キー: 店舗管理マニュアル背面ラベル参照\n"
                "・同時接続上限: 20台"
            )
            suggested_followups = [
                "店舗ネットワーク保守センターへ交換機材の手配を依頼する",
                "ONUのアラームランプが赤点滅している時は？",
                "直りました、ありがとうございます",
            ]
        elif is_detail_request:
            answer_text = (
                "【対応指示】\n"
                "スイッチ本体の『POWER』ランプが緑点灯、各ポートの『給電』ランプが橙点灯になっているか確認してください。\n\n"
                "【理由】\n"
                "スイッチ本体および接続先の無線機器に電気が正しく供給されているかを一目で確認するためです。"
            )
            technical_details = (
                "【ネットワークスイッチ（SW-BY-01）LED確認手順】\n"
                "1. POWERランプ: 緑点灯＝給電正常（消灯または赤点灯はスイッチ本体不良）\n"
                "2. ポート1〜8の給電LED: 橙点灯＝正常給電中（消灯＝給電停止）\n"
                "3. 無線AP（AP-BY-01）のSTATUSランプ: 電源投入後約3分で緑点灯に変わることを確認"
            )
            suggested_followups = [
                "ネットワークスイッチを再起動しても無線APのランプが点灯しません",
                "緊急用モバイルルーター（LTE）への切り替え手順を詳しく",
                "店舗ネットワーク保守センターへ連絡する",
            ]
        else:
            answer_text = (
                "【対応指示】\n"
                "バックヤード通信ラック内にあるネットワークスイッチ（SW-BY-01）の電源ケーブルを抜き、15秒待ってから差し直してください。\n\n"
                "【理由】\n"
                "スイッチの給電機能が一時的に固まり、無線Wi-Fi機器が停止しているため、電源の再投入で復帰させるためです。\n\n"
                "復旧しない場合やONUに赤ランプが点灯している場合は、店舗ネットワーク保守センター（内線: #5100）へエスカレーションしますか？"
            )
            technical_details = (
                "【システム診断詳細】\n"
                "・検出ログ: NET-SW-DOWN (SNMPトラップ)\n"
                "・対象機器: ネットワークスイッチ (SW-BY-01) / 無線AP (AP-BY-01)\n"
                "・参照チケット: #11980 (類似度91%)\n"
                "・想定復旧時間: 約3分"
            )
            suggested_followups = [
                "ネットワークスイッチを再起動しても無線APのランプが点灯しません",
                "ネットワークスイッチ再起動後のLEDランプ状態確認の詳細手順は？",
                "店舗ネットワーク保守センターへ連絡する",
            ]

    # 続けて質問の候補に専用回答がある場合は、汎用の意図分岐より候補専用の回答を優先する
    if is_followup and prev_ctx and matched_followup and not has_image:
        specific = get_specific_followup_answer(matched_followup, domain, tickets)
        if specific:
            answer_text, technical_details, suggested_followups = specific

    # 更問いの応答調整（デモ向け: 「ご質問の意図を特定できませんでした」等の聞き返しは出さない）
    # ・相づち／原因／過去事例／状況／お客様説明／連絡先 → 会話的な回答
    # ・それ以外の漠然とした更問い → 現在の手順の詳細に「次の手順をご案内します」を添える
    # ・過去と同じ回答になる場合 → 「改めてご案内します」を添えて再回答
    clarification_reason: str | None = None
    answer_history: list[str] = []
    conversational_note = ""
    if is_followup and prev_ctx:
        answer_history = list(prev_ctx.get("answer_history") or [prev_ctx.get("last_answer", "")])
        if not has_image and conversational_kind:
            conv = build_conversational_answer(
                conversational_kind,
                agent_label,
                top_ticket,
                tickets,
                str(prev_ctx.get("last_answer", "")),
                runbook.get("escalation_team", "") if isinstance(runbook, dict) else "",
            )
            if conv:
                answer_text, technical_details = conv
                suggested_followups = list(prev_followups or suggested_followups)
                conversational_note = f"口語の更問い（{conversational_kind}）として会話の流れで回答。"
            elif conversational_kind == "next":
                answer_text = with_lead(answer_text, "状況を踏まえ、次の手順をご案内します。")
                conversational_note = "続けて質問と一致しないため、現在の手順の詳細で回答。"
        if not has_image and answer_text in answer_history:
            answer_text = with_lead(answer_text, "改めてご案内します。")
            conversational_note = (conversational_note + " 過去と同じ内容のため「改めて」を添えて再回答。").strip()

        match_note = (
            f"続けて質問「{matched_followup}」と照合し、その意図で回答。"
            if matched_followup
            else conversational_note
        )
        intent_step = next((s for s in reasoning_steps if s.get("type") == "intent_classification"), None)
        if intent_step is not None:
            if match_note:
                intent_step["detail"] = f"{intent_step['detail']} {match_note}"
            intent_step["evidence"].append({
                "followup_matching": {
                    "matched_followup": matched_followup,
                    "score": match_info.get("score"),
                    "reason": match_info.get("reason"),
                    "runner_up": match_info.get("runner_up"),
                    "clarification": clarification_reason,
                }
            })

    ctx = {
        "domain": domain,
        "agent_id": agent_id,
        "agent_label": agent_label,
        "top_ticket": top_ticket,
        "tickets": tickets,
        "logs": logs,
        "runbook": runbook,
        "last_answer": answer_text,
        "answer_history": (answer_history + [answer_text])[-10:],
        "technical_details": technical_details,
        "suggested_followups": suggested_followups,
        "image_data": image_data,
    }
    SESSION_CONTEXT[session_id] = ctx
    LATEST_SESSION_CONTEXT = ctx

    return {
        "status": "success",
        "session_id": session_id,
        "query": query,
        "domain": domain,
        "assigned_agent": agent_id,
        "assigned_agent_label": agent_label,
        "is_followup": bool(is_followup),
        "answer": answer_text,
        "technical_details": technical_details,
        "reasoning_steps": reasoning_steps,
        "tickets": tickets,
        "logs": logs,
        "runbook": runbook,
        "escalation_team": runbook["escalation_team"],
        "prompt_to_user": (
            "候補の番号か、近い言葉でお答えください。"
            if clarification_reason
            else "解決しない場合は有人サポートへエスカレーションしますか？"
        ),
        "suggested_followups": suggested_followups,
        "image_data": image_data,
        "matched_followup": matched_followup,
        "match_score": match_info.get("score"),
        "clarification": clarification_reason,
    }

