"""Layer 3: インメモリ RAG データ基盤 (Mock BigQuery / Splunk / 過去チケット / Runbook).

SPEC.md の機能要件 3.3, 3.4, 5 に基づき、以下の4つの専門領域のナレッジデータを
スタンドアロン・インメモリで保持し、ハイブリッド（ベクトル風特徴量＋キーワード）検索を提供します。

- AGENT 01 (`infra_specialist`): インフラスペシャリスト（サーバー・CPU逼迫・リソース障害）
- AGENT 02 (`order_specialist`): 発注管理スペシャリスト（生鮮品発注データ未反映・EDI連携エラー）
- AGENT 03 (`network_specialist`): 店舗ネットワークスペシャリスト（店舗WAN/LAN・バックヤード疎通不良）
- AGENT 04 (`pos_specialist`): POS・スマートストアスペシャリスト（スマートカートゲート・電子棚札・ゴールデンパス）
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class IncidentTicket:
    """過去の障害対応チケット (BigQuery 集約ナレッジの Mock)."""

    ticket_id: str
    domain: str  # "infra" | "order" | "network" | "pos"
    title: str
    store_or_system: str
    device_model: str
    symptom: str
    root_cause: str
    resolution: str
    occurred_at: str
    resolved_in_minutes: int
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SplunkLogEntry:
    """Splunk から BigQuery に集約されたシステムエラーログ (Mock)."""

    log_id: str
    timestamp: str
    domain: str
    host: str
    source: str
    level: str
    error_code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryRunbook:
    """標準一次切り分け・復旧手順書 (Runbook)."""

    runbook_id: str
    domain: str
    target_system: str
    title: str
    steps: list[str]
    escalation_criteria: str
    escalation_team: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =====================================================================
# 1. 過去インシデントチケット (Mock Data)
# =====================================================================
MOCK_TICKETS: list[IncidentTicket] = [
    # --- AGENT 04: POS / スマートストアスペシャリスト (カメラ画像ビジュアル診断: チケット #12350) ---
    IncidentTicket(
        ticket_id="#12350",
        domain="pos",
        title="【カメラ画像AIビジュアル診断】店頭機器LEDランプ赤点滅・端末エラー画面の画像解析と即時復旧手順",
        store_or_system="店舗現場全般 / スマートカートゲート・電子棚札・ネットワークスイッチ等",
        device_model="現場設置機器全般 (GATE-AP-02, ESL-GW-DELI-01, SW-BACKYARD-01 等)",
        symptom="現場スタッフが機器名やトラブルの専門用語を把握していない状況で、手元のカメラ・スマートフォンで機器のLEDランプ状態や端末エラー画面を撮影。AI画像解析により障害機器と原因を即座に特定した。",
        root_cause="カメラ撮影画像から、ゲート制御ボックス内の無線通信レシーバー（GATE-AP-02）のERRランプ赤点滅、およびスマートカート画面の通信エラー表示（ERR-GATE-408）を視覚的に検出。",
        resolution="AI画像解析の指示に従い、撮影された無線レシーバーの電源スイッチを10秒間OFF/ONして再起動。40秒後にLINKランプが正常（緑点灯）に復帰し、カート端末の通過通信が正常化した。",
        occurred_at="2026-09-28 10:15:00",
        resolved_in_minutes=5,
        keywords=[
            "カメラ",
            "撮影",
            "写真",
            "画像",
            "ビジュアル",
            "写真見て",
            "カメラで見て",
            "ランプ見て",
            "エラー画面撮った",
            "これ見て",
            "赤点滅",
            "画像解析",
            "写した",
            "12350",
            "診断",
            "カメラ起動",
            "撮影した画像",
            "写真で診断",
            "画像で診断",
        ],
    ),
    # --- AGENT 04: POS / スマートストアスペシャリスト (チケット #12345) ---
    IncidentTicket(
        ticket_id="#12345",
        domain="pos",
        title="【ERR-GATE-408 / 通信タイムアウト】スマートカート専用ゲート通過時に通信が完了せず画面が停止する",
        store_or_system="中央店 (スマートカート専用 2番ゲート)",
        device_model="スマートカート Gen3 / 専用ゲート無線レシーバー (GATE-AP-02)",
        symptom="お客様がスマートカートを押して2番専用ゲートを通過した際、カートとゲート間の通信が完了せず、カート画面が『ERR-GATE-408: スタッフをお呼びください』で停止し、ゲートバーが開かない。",
        root_cause="先月も同店舗で夕方の混雑時間帯に、ゲート上部の無線通信レシーバー（GATE-AP-02）のBLE/Wi-Fiセッションテーブルが飽和し、通過検知パケットのタイムアウトが発生していた。",
        resolution="一次切り分けとして、アテンダント端末で通信状態を確認の上、ゲート脇制御ボックス内の無線通信レシーバー（GATE-AP-02）の電源を10秒間OFF/ONして再起動し、カート画面の『再通信』を押下したところ正常通過が復旧した。",
        occurred_at="2026-08-19 17:40:00",
        resolved_in_minutes=10,
        keywords=[
            "中央店",
            "スマートカート",
            "スマートショッピングカート",
            "専用ゲート",
            "ゲート",
            "2番ゲート",
            "2番",
            "フリーズ",
            "err-gate-408",
            "タイムアウト",
            "レシーバー",
            "再起動",
            "12345",
            "〇〇店",
            "通過",
            "固まった",
            "固まる",
            "通れない",
            "出られない",
            "ゲートが開かない",
            "ゲートで止まった",
            "スタッフ呼出",
        ],
    ),
    IncidentTicket(
        ticket_id="#12110",
        domain="pos",
        title="【ESL-SYNC-501 / 値引表示未更新】惣菜売場の電子棚札に夕方のAI自動値下げ価格が反映されない",
        store_or_system="駅前店 (惣菜売場 電子棚札 ESL / AIカメラ連動)",
        device_model="電子棚札 (ESL-Tag-2.9) / 無線基地局 (ESL-GW-DELI-01)",
        symptom="夕方19時のタイムセール時間帯になっても、惣菜コーナーの電子棚札（ESL）が『20%引・半額』のAI自動値下げ表示に切り替わらず、定価表示のままとなる。",
        root_cause="惣菜売場天井のESL無線基地局（ESL-GW-DELI-01）のSub-GHz送信キューがスタックし、AIカメラ連携ダイナミックプライシングの価格更新パケットが配信停止していた。",
        resolution="惣菜バックヤード壁面の通信盤内にあるESL基地局（ESL-GW-DELI-01）のLANケーブルを抜き差しして再起動後、店舗管理タブレットから『電子棚札 価格一括再送』を実行し全件更新完了。",
        occurred_at="2026-08-04 19:15:00",
        resolved_in_minutes=15,
        keywords=[
            "電子棚札",
            "esl",
            "棚札",
            "値札",
            "電子値札",
            "値下げ",
            "値引き",
            "割引",
            "半額",
            "惣菜",
            "お惣菜",
            "タイムセール",
            "定価",
            "変わらない",
            "更新されない",
            "値段",
            "価格",
            "価格表示",
            "esl-sync-501",
            "基地局",
            "駅前店",
            "12110",
        ],
    ),
    IncidentTicket(
        ticket_id="#12085",
        domain="pos",
        title="【ERR-CART-WGT-105 / 重量検知エラー】スマートカートで商品スキャン後に「スキャン漏れ検知」が頻発し操作不能になる",
        store_or_system="東店 (スマートショッピングカート #042)",
        device_model="スマートカート Gen3 (重量センサー & スキャン漏れ検知AIカメラ搭載)",
        symptom="お客様が正しく商品をバーコードスキャンしてカゴに入れているにもかかわらず、『スキャンされていない商品が検知されました（係員をお呼びください）』のアラートが繰り返し表示され買い物が継続できない。",
        root_cause="カート下部カゴ受け皿の重量センサーの風袋（ゼロ点）ドリフト、およびカゴ上部検知カメラレンズの結露・汚れによる誤判定。",
        resolution="アテンダント用マスターバーコードをスキャンして保守メニューを開き、『重量センサーゼロ点リセット（キャリブレーション）』を実施するとともにカメラレンズを清掃して復旧。",
        occurred_at="2026-07-28 15:20:00",
        resolved_in_minutes=8,
        keywords=[
            "スマートカート",
            "カート",
            "重量",
            "重量センサー",
            "スキャン漏れ",
            "スキャン",
            "カゴ",
            "係員",
            "店員",
            "係員呼出",
            "未スキャン",
            "検知エラー",
            "err-cart-wgt-105",
            "キャリブレーション",
            "ゼロ点",
            "ピーピー",
            "東店",
            "12085",
        ],
    ),
    # --- AGENT 01: インフラスペシャリスト ---
    IncidentTicket(
        ticket_id="#10892",
        domain="infra",
        title="【INF-CPU-100 / 応答遅延】在庫・AIカメラ欠品検知APサーバー高負荷によるAPI応答停止 (504 Timeout)",
        store_or_system="クラウド・店舗エッジ基盤 在庫管理APサーバー (ap-inv-prod-02 / GKE NodePool)",
        device_model="Compute Engine c3-standard-8 / Container Worker",
        symptom="APサーバーのCPU使用率が100%に張り付き、店舗タブレットへの棚欠品検知アラートおよび在庫照会APIレスポンスが504 Gateway Timeoutとなる。",
        root_cause="リアルタイム在庫引当・AIカメラ解析バッチの無限リトライループによりワーカープロセスのスレッドが飽和。",
        resolution="一次切り分けとして、該当ワーカーPod（inventory-sync-worker）のローリング再起動を実施し、滞留ジョブをデッドレターキューへ退避して復旧。",
        occurred_at="2026-08-03 10:05:00",
        resolved_in_minutes=15,
        keywords=[
            "サーバー",
            "サーバ",
            "CPU",
            "100%",
            "張り付",
            "高負荷",
            "リソース",
            "メモリ",
            "インフラ",
            "aiカメラ",
            "欠品",
            "ap-inv-prod",
            "504",
            "タイムアウト",
            "gateway timeout",
            "重い",
            "応答しない",
            "遅い",
            "落ちた",
            "落ちてる",
            "在庫管理",
            "pod",
            "ポッド",
            "gke",
            "inf-cpu-100",
            "ダウン",
            "10892",
        ],
    ),
    # --- AGENT 02: 発注管理スペシャリスト ---
    IncidentTicket(
        ticket_id="#11403",
        domain="order",
        title="【EDI-ERR-302 / 発注未反映】青果・鮮魚・精肉のAI自動発注データが基幹・取引先EDIに未反映",
        store_or_system="受発注管理システム (OrderHub / AI需要予測自動発注・EDI連携)",
        device_model="OrderHub Batch v4.2 (Cloud Run Jobs)",
        symptom="各店舗（中央店・西店等）の当日生鮮品（青果・鮮魚・精肉）の自動発注データが基幹システムおよび卸売EDIに反映されていない。",
        root_cause="朝の特売マスタ更新時に新商品JANコードの桁数バリデーションエラー（EDI-ERR-302）が発生し、生鮮カテゴリ全体の連携バッチがロールバック中断していた。",
        resolution="一次切り分けとして、エラー対象レコードをスキップ退避の上、管理コンソールから『生鮮EDI差分再連携ジョブ（job-order-resync-fresh）』を手動再実行して30分以内に全件反映。",
        occurred_at="2026-08-11 07:30:00",
        resolved_in_minutes=25,
        keywords=[
            "生鮮品",
            "生鮮",
            "発注",
            "発注データ",
            "自動発注",
            "反映されない",
            "反映されていない",
            "未反映",
            "受発注",
            "サプライチェーン",
            "EDI",
            "青果",
            "鮮魚",
            "精肉",
            "野菜",
            "魚",
            "肉",
            "果物",
            "届いてない",
            "通ってない",
            "edi-err-302",
            "連携エラー",
            "バッチ",
            "11403",
        ],
    ),
    # --- AGENT 03: 店舗ネットワークスペシャリスト ---
    IncidentTicket(
        ticket_id="#11980",
        domain="network",
        title="【NET-SW-DOWN / Wi-Fi切断】バックヤード検品ハンディ・品出しタブレットの通信不可",
        store_or_system="西店 バックヤードLAN・Wi-Fi設備",
        device_model="Cisco Catalyst Meraki MS120-8LP (ネットワークスイッチ) / MR46 AP",
        symptom="店舗バックヤードでネットワークが繋がらず、入荷検品ハンディターミナル（BHT）および品出し業務用タブレットがオフラインとなり通信不可。",
        root_cause="バックヤード配下のネットワークスイッチ（SW-BY-01）の電源供給トラブルにより、接続された無線アクセスポイント（AP-BY-01）がリンクダウン。",
        resolution="一次切り分けとして、バックヤード通信ラック内のネットワークスイッチ（SW-BY-01）の電源再投入（15秒待機）およびLANケーブル確認を実施し、リンクアップ復旧。",
        occurred_at="2026-08-25 09:10:00",
        resolved_in_minutes=14,
        keywords=[
            "バックヤード",
            "ネットワーク",
            "繋がらない",
            "つながらない",
            "通信",
            "疎通",
            "Wi-Fi",
            "wifi",
            "LAN",
            "WAN",
            "店舗",
            "スイッチ",
            "ハンディ",
            "タブレット",
            "検品",
            "品出し",
            "圏外",
            "オフライン",
            "poe",
            "アクセスポイント",
            "事務所",
            "net-poe-down",
            "net-sw-down",
            "西店",
            "11980",
        ],
    ),
]


# =====================================================================
# 2. Splunk エラーログ (Mock Data)
# =====================================================================
MOCK_SPLUNK_LOGS: list[SplunkLogEntry] = [
    # --- POS / スマートストア (AGENT 04) ---
    SplunkLogEntry(
        log_id="SPL-POS-99201",
        timestamp="2026-09-28 17:32:11.402",
        domain="pos",
        host="chuo-cartgate-02",
        source="/var/log/smartcart/gate_comm.log",
        level="ERROR",
        error_code="ERR-GATE-408",
        message="GatePassTimeoutException: Smart cart pass-through communication timed out after 8000ms at Gate #2 (Receiver: GATE-AP-02, Cart: SSC-018). Gate open aborted. Similar to incident #12345.",
        metadata={
            "store": "中央店",
            "gate_no": "スマートカート専用 2番ゲート",
            "receiver_host": "GATE-AP-02",
            "action_recommended": "Reboot gate wireless receiver GATE-AP-02",
        },
    ),
    SplunkLogEntry(
        log_id="SPL-POS-99202",
        timestamp="2026-09-28 17:32:28.118",
        domain="pos",
        host="chuo-gate-ap-02",
        source="syslog:gate-ap-02",
        level="WARN",
        error_code="BLE-SESS-FULL",
        message="Gate wireless receiver GATE-AP-02 BLE/Wi-Fi session table reached 99.6%. Dropping new cart pass-through beacon packets.",
        metadata={
            "store": "中央店",
            "device": "GATE-AP-02",
            "action_recommended": "Power cycle GATE-AP-02",
        },
    ),
    # --- INFRA (AGENT 01) ---
    SplunkLogEntry(
        log_id="SPL-INF-88104",
        timestamp="2026-09-28 17:25:03.910",
        domain="infra",
        host="ap-inv-prod-02",
        source="/var/log/containers/inventory-sync-worker.log",
        level="CRITICAL",
        error_code="INF-CPU-100",
        message="SystemMonitorAlert: CPU utilization sustained at 100.0% for 12m on ap-inv-prod-02. Worker thread pool exhausted in InventorySyncWorker (Deadlock retry storm).",
        metadata={
            "cpu_usage_pct": 100.0,
            "load_average_1m": 16.42,
            "top_process": "python -m app.workers.inventory_sync",
        },
    ),
    # --- ORDER (AGENT 02) ---
    SplunkLogEntry(
        log_id="SPL-ORD-77319",
        timestamp="2026-09-28 17:10:44.205",
        domain="order",
        host="orderhub-batch-prod",
        source="/var/log/orderhub/edi_sync.log",
        level="ERROR",
        error_code="EDI-ERR-302",
        message="EDIBatchRollbackException: Fresh food order batch (category=FRESH_PRODUCE) aborted due to invalid JAN check-digit in record #4412. 128 store orders pending resync.",
        metadata={
            "batch_job": "job-order-resync-fresh",
            "affected_category": "生鮮品 (青果・鮮魚・精肉)",
            "pending_records": 128,
        },
    ),
    # --- NETWORK (AGENT 03) ---
    SplunkLogEntry(
        log_id="SPL-NET-66410",
        timestamp="2026-09-28 17:28:50.004",
        domain="network",
        host="nishi-store-by-sw-01",
        source="snmp-trap:meraki-ms120",
        level="ERROR",
        error_code="NET-SW-DOWN",
        message="Interface Gi1/0/4 (Backyard Wi-Fi AP-BY-01) link down: PoE controller fault detected on switch SW-BY-01. Backyard subnet 10.24.80.0/24 unreachable.",
        metadata={
            "store": "西店",
            "location": "店舗バックヤード",
            "switch_id": "SW-BY-01",
            "ap_id": "AP-BY-01",
            "status": "LINK_DOWN",
        },
    ),
]


# =====================================================================
# 3. 復旧手順書 (Runbook Mock Data)
# =====================================================================
MOCK_RUNBOOKS: list[RecoveryRunbook] = [
    RecoveryRunbook(
        runbook_id="RB-POS-004",
        domain="pos",
        target_system="スマートカート専用ゲート / ゲート無線通信レシーバー (GATE-AP-02)",
        title="スマートカート専用ゲート通過時の通信タイムアウト・画面停止一次切り分け手順",
        steps=[
            "1. アテンダント用タブレットにて該当カート番号の通信状態を確認する。",
            "2. 2番ゲート脇の制御ボックス内にある『ゲート無線通信レシーバー（GATE-AP-02）』の電源スイッチをOFFにし、10秒待機してから再度ONにして再起動する（約40秒でLINKランプ緑点灯）。",
            "3. お客様のスマートカート画面に表示されている『再通信（リトライ）』ボタンを押下し、通過認証の完了とゲートバー開放を確認する。",
            "4. カート個体の重量センサー誤検知（ERR-CART-WGT-105）の場合は、店員バーコードで保守画面を開き『重量センサーゼロ点リセット』を実行する。",
        ],
        escalation_criteria="ゲート通信レシーバー再起動後も複数台のスマートカートで通信エラーが継続する場合は、スマートストア・POS有人サポートデスクへ即時エスカレーションする。",
        escalation_team="スマートストア・POS有人サポートデスク (内線: #4400 / メーカー保守連携)",
    ),
    RecoveryRunbook(
        runbook_id="RB-VISUAL-001",
        domain="pos",
        target_system="店舗現場機器全般 (ゲートレシーバー GATE-AP-02, ESL基地局, ネットワークスイッチ等)",
        title="カメラ撮影・画像AIビジュアル診断 一次切り分け手順書",
        steps=[
            "1. 撮影画像から特定された対象機器（例: GATE-AP-02）の外観およびLEDインジケーター（ERR赤点滅）を確認する。",
            "2. 制御ボックス内の電源スイッチまたはLANケーブルを抜き差しし、10秒間放電後に再起動する。",
            "3. 約40秒待機し、LINKランプが緑点灯（正常通信状態）へ切り替わることを目視確認する。",
            "4. 端末側の画面で『再試行』または『再通信』を押下し、正常稼働を確認する。",
        ],
        escalation_criteria="再起動後もLEDランプが赤点滅を継続する場合、または物理的破損が確認された場合は、スマートストア有人サポートデスク（内線: #4300）へエスカレーションする。",
        escalation_team="スマートストア・POS有人サポートデスク (内線: #4300 / メーカー保守連携)",
    ),
    RecoveryRunbook(
        runbook_id="RB-INF-001",
        domain="infra",
        target_system="クラウド・APサーバー基盤 (ap-inv-prod)",
        title="在庫・AIカメラ解析APサーバー CPU 100% 張り付き時の一次切り分け手順",
        steps=[
            "1. Cloud Monitoring ダッシュボードで該当ノード/PodのCPU・スレッド滞留状況を確認する。",
            "2. 無限リトライ中のワーカープロセス（inventory-sync-worker）を特定し、ローリング再起動コマンドを実行する。",
            "3. デッドレターキューに退避された失敗ジョブのペイロードを確認し、CPU負荷が30%以下に低下したことを確認する。",
        ],
        escalation_criteria="ワーカー再起動後5分以内に再度CPU 100%へ達する場合、またはDBデッドロックが解消しない場合は、インフラSRE二次対応チームへエスカレーションする。",
        escalation_team="クラウドSRE 有人オンコールチーム (PagerDuty: #sre-oncall)",
    ),
    RecoveryRunbook(
        runbook_id="RB-ORD-002",
        domain="order",
        target_system="受発注管理システム (OrderHub / 生鮮EDI連携)",
        title="生鮮品AI自動発注データ未反映時の一次切り分け・再送手順",
        steps=[
            "1. OrderHub 管理画面の『バッチ実行モニター』を開き、エラーコード『EDI-ERR-302』の停止ログを確認する。",
            "2. フォーマット不備の特売マスタレコードを『一時保留（スキップ）』に設定する。",
            "3. 再送ジョブ『job-order-resync-fresh（生鮮EDI差分再連携）』を実行し、ステータスがCOMPLETEDになることを確認する。",
        ],
        escalation_criteria="再送ジョブが再度失敗する場合、または発注締切時刻（午前9:00）まで残り30分を切っている場合は、サプライチェーン運用有人デスクへ緊急エスカレーションする。",
        escalation_team="受発注・EDI運用サポートデスク (内線: #3200)",
    ),
    RecoveryRunbook(
        runbook_id="RB-NET-003",
        domain="network",
        target_system="店舗バックヤードLAN / ネットワークスイッチ (SW-BY-01)",
        title="店舗バックヤード ネットワーク疎通不良時の一次切り分け手順",
        steps=[
            "1. バックヤード事務所内の通信ラックにあるネットワークスイッチ（SW-BY-01）のポートLED（特に4番ポート: 無線AP接続）を確認する。",
            "2. LANケーブルの緩みがないか確認し、ネットワークスイッチ本体の電源ケーブルを抜いて15秒待機後に再投入する。",
            "3. 約3分後、天井の無線AP（AP-BY-01）のLEDが緑点灯に変わり、業務用タブレット・検品ハンディがWi-Fi接続できることを確認する。",
        ],
        escalation_criteria="スイッチ再起動後もリンクアップしない場合、またはWAN回線終端装置（ONU）のALARMランプが赤点灯している場合は、店舗ネットワーク有人保守窓口へエスカレーションする。",
        escalation_team="店舗ネットワーク保守センター (内線: #5100 / キャリア保守連携)",
    ),
]


# =====================================================================
# 4. セマンティック / ハイブリッド検索エンジン
# =====================================================================
def _tokenize(text: str) -> list[str]:
    """日本語・英数字の簡易N-gramおよび単語トークナイズ."""
    normalized = text.lower()
    words = re.findall(r"[a-z0-9_#-]+|[ぁ-んァ-ヶー一-龠々〇]+", normalized)
    tokens: list[str] = []
    for w in words:
        tokens.append(w)
        # 日本語文字列の場合は2-gramも生成して部分一致精度を高める
        if re.search(r"[ぁ-んァ-ヶー一-龠々〇]", w) and len(w) >= 2:
            for i in range(len(w) - 1):
                tokens.append(w[i : i + 2])
    return tokens


def _compute_similarity(query: str, target_text: str, boost_keywords: list[str] | None = None) -> float:
    """クエリと対象テキストのハイブリッド類似度スコア (0.0 ~ 1.0) を算出する."""
    q_tokens = set(_tokenize(query))
    t_tokens = set(_tokenize(target_text))
    if not q_tokens or not t_tokens:
        return 0.0

    intersection = q_tokens.intersection(t_tokens)
    cosine_like = len(intersection) / math.sqrt(len(q_tokens) * len(t_tokens))

    # キーワードブースト
    keyword_score = 0.0
    q_lower = query.lower()
    if boost_keywords:
        matched_kw = sum(1 for kw in boost_keywords if kw.lower() in q_lower)
        if matched_kw > 0:
            keyword_score = min(0.65, matched_kw * 0.18)

    score = min(0.99, round(cosine_like * 0.6 + keyword_score, 3))
    return score


def classify_domain(query: str) -> dict[str, Any]:
    """問い合わせテキストから専門ドメインと担当エージェントを判定する."""
    q = query.lower()
    domain_rules = [
        (
            "pos",
            "pos_specialist",
            "AGENT 04: POS スペシャリスト",
            # strong_terms (重み 2.0)
            [
                "スマートカート",
                "スマートカート",
                "専用ゲート",
                "電子棚札",
                "棚札",
                "値札",
                "電子値札",
                "スキャン漏れ",
                "セルフレジ",
                "su-pay",
                "esl",
                "err-gate-408",
                "err-cart-wgt-105",
                "esl-sync-501",
                "12345",
                "12110",
                "12085",
            ],
            # regular_terms (重み 1.0)
            [
                "カート",
                "ゲート",
                "値下げ",
                "値引き",
                "割引",
                "半額",
                "惣菜",
                "お惣菜",
                "タイムセール",
                "定価",
                "値段",
                "価格",
                "重量",
                "スキャン",
                "カゴ",
                "レジ",
                "端末",
                "電子マネー",
                "pos",
                "会計",
                "レシート",
                "通過",
                "固まった",
                "固まる",
                "通れない",
                "出られない",
                "係員",
                "ゼロ点",
                "キャリブレーション",
            ],
        ),
        (
            "infra",
            "infra_specialist",
            "AGENT 01: インフラ スペシャリスト",
            # strong_terms
            [
                "ap-inv",
                "504",
                "gateway timeout",
                "inf-cpu-100",
                "gke",
                "10892",
                "pod",
                "ポッド",
            ],
            # regular_terms
            [
                "cpu",
                "サーバー",
                "サーバ",
                "メモリ",
                "100%",
                "張り付",
                "インフラ",
                "高負荷",
                "db",
                "データベース",
                "応答しない",
                "重い",
                "タイムアウト",
                "落ちた",
                "落ちてる",
                "ダウン",
                "スレッド",
                "在庫管理",
                "aiカメラ",
            ],
        ),
        (
            "order",
            "order_specialist",
            "AGENT 02: 発注管理 スペシャリスト",
            # strong_terms
            [
                "受発注",
                "edi",
                "自動発注",
                "edi-err-302",
                "orderhub",
                "11403",
            ],
            # regular_terms
            [
                "発注",
                "生鮮",
                "生鮮品",
                "青果",
                "鮮魚",
                "精肉",
                "野菜",
                "魚",
                "肉",
                "果物",
                "サプライチェーン",
                "未反映",
                "反映されない",
                "反映されていない",
                "届いてない",
                "通ってない",
                "納品",
                "仕入れ",
                "バッチ",
            ],
        ),
        (
            "network",
            "network_specialist",
            "AGENT 03: 店舗ネットワーク スペシャリスト",
            # strong_terms
            [
                "net-poe-down",
                "net-sw-down",
                "11980",
                "poe",
                "アクセスポイント",
                "sw-by",
                "onu",
                "光回線",
                "インターネット",
                "ap-by",
            ],
            # regular_terms
            [
                "バックヤード",
                "ネットワーク",
                "ネット",
                "繋がらない",
                "つながらない",
                "疎通",
                "wi-fi",
                "wifi",
                "lan",
                "wan",
                "スイッチ",
                "オフライン",
                "ハンディ",
                "タブレット",
                "検品",
                "品出し",
                "圏外",
                "事務所",
                "通信",
                "回線",
                "不通",
            ],
        ),
    ]

    best_domain = "pos"
    best_agent_id = "pos_specialist"
    best_agent_label = "AGENT 04: POS・スマートストアスペシャリスト"
    best_score = 0.0
    matched_terms: list[str] = []

    for domain, agent_id, label, strong_terms, regular_terms in domain_rules:
        strong_hits = [t for t in strong_terms if t in q]
        regular_hits = [t for t in regular_terms if t in q]
        all_hits = strong_hits + regular_hits
        score = len(strong_hits) * 2.0 + len(regular_hits) * 1.0

        if score > best_score:
            best_score = score
            best_domain = domain
            best_agent_id = agent_id
            best_agent_label = label
            matched_terms = all_hits

    confidence = min(0.98, 0.75 + best_score * 0.08) if best_score > 0 else 0.45
    needs_immediate_escalation = best_score == 0 or "有人" in q or "エスカレーション" in q or "解決しない" in q

    return {
        "domain": best_domain,
        "agent_id": best_agent_id,
        "agent_label": best_agent_label,
        "confidence": round(confidence, 2),
        "matched_terms": matched_terms,
        "needs_immediate_escalation": needs_immediate_escalation,
    }


def search_tickets(query: str, domain: str | None = None, top_k: int = 2) -> list[dict[str, Any]]:
    """過去の対応チケットから類似度の高い事例を検索する."""
    candidates = [t for t in MOCK_TICKETS if domain is None or t.domain == domain]
    if not candidates:
        candidates = list(MOCK_TICKETS)

    q_lower = query.lower()
    scored: list[tuple[float, IncidentTicket]] = []
    for ticket in candidates:
        text = f"{ticket.title} {ticket.store_or_system} {ticket.symptom} {ticket.root_cause} {ticket.resolution}"
        score = _compute_similarity(query, text, ticket.keywords)

        # チケット固有シグネチャ単語のブースト (POS内の3ユースケースおよび他領域の正確な判別)
        ticket_signatures = {
            "#12110": ["棚札", "値札", "電子値札", "esl", "惣菜", "お惣菜", "値下げ", "値引き", "割引", "半額", "タイムセール", "定価", "esl-sync-501"],
            "#12085": ["重量", "スキャン漏れ", "カゴ", "係員", "ゼロ点", "キャリブレーション", "err-cart-wgt-105", "未スキャン", "ピーピー"],
            "#12345": ["ゲート", "専用ゲート", "通過", "固まった", "固まる", "通過認証", "タイムアウト", "err-gate-408", "2番ゲート", "レシーバー", "レシート", "通れない", "出られない"],
            "#10892": ["cpu", "100%", "サーバー", "サーバ", "ap-inv", "504", "gateway timeout", "inf-cpu-100", "高負荷", "張り付", "重い", "応答しない", "pod", "ポッド"],
            "#11403": ["発注", "生鮮", "青果", "鮮魚", "精肉", "野菜", "魚", "肉", "edi", "edi-err-302", "orderhub", "届いてない", "通ってない", "未反映", "反映されない"],
            "#11980": ["バックヤード", "ハンディ", "タブレット", "ネット", "ネットワーク", "繋がらない", "つながらない", "wi-fi", "wifi", "poe", "スイッチ", "圏外", "オフライン", "net-poe-down", "net-sw-down"],
        }
        sig_matches = sum(1 for s in ticket_signatures.get(ticket.ticket_id, []) if s.lower() in q_lower)
        if sig_matches > 0:
            score = min(0.99, score + sig_matches * 0.12)

        if domain and ticket.domain == domain:
            score = min(0.99, score + 0.15)
        scored.append((score, ticket))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, ticket in scored[:top_k]:
        item = ticket.to_dict()
        item["similarity_score"] = round(score, 3)
        results.append(item)
    return results


def search_logs(query: str, domain: str | None = None, top_k: int = 2) -> list[dict[str, Any]]:
    """Splunk エラーログから関連性の高いログエントリを抽出する."""
    candidates = [log for log in MOCK_SPLUNK_LOGS if domain is None or log.domain == domain]
    if not candidates:
        candidates = list(MOCK_SPLUNK_LOGS)

    scored: list[tuple[float, SplunkLogEntry]] = []
    for log in candidates:
        text = f"{log.host} {log.error_code} {log.message} {log.metadata}"
        score = _compute_similarity(query, text)
        if domain and log.domain == domain:
            score = max(score, 0.85)
        scored.append((score, log))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, log in scored[:top_k]:
        item = log.to_dict()
        item["relevance_score"] = round(score, 3)
        results.append(item)
    return results


def get_runbook(domain: str) -> dict[str, Any]:
    """指定ドメインの標準復旧手順書（Runbook）を取得する."""
    for rb in MOCK_RUNBOOKS:
        if rb.domain == domain:
            return rb.to_dict()
    return MOCK_RUNBOOKS[0].to_dict()


DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "scenario-infra",
        "badge": "🖥️ インフラ (AGENT 01)",
        "agent_id": "infra_specialist",
        "agent_name": "インフラ スペシャリスト",
        "title": "在庫APサーバーCPU100% (#10892)",
        "prompt": "在庫・AIカメラ欠品検知APサーバー（ap-inv-prod-02）のCPU使用率が100%に張り付いて504タイムアウトが多発しています。一次対処を教えてください。",
        "description": "チケット#10892とコンテナログから無限リトライによるスレッド飽和を特定し、ローリング再起動手順を提示します。",
    },
    {
        "id": "scenario-order",
        "badge": "📦 発注管理 (AGENT 02)",
        "agent_id": "order_specialist",
        "agent_name": "発注管理 スペシャリスト",
        "title": "生鮮品AI自動発注未反映 (#11403)",
        "prompt": "今日の生鮮品（青果・鮮魚・精肉）のAI自動発注データが基幹システムに反映されていないのですが、エラーは出ていますか？（EDI-ERR-302）",
        "description": "チケット#11403とEDIバッチログから特売マスタ桁数エラーを特定し、差分再送ジョブ手順を提示します。",
    },
    {
        "id": "scenario-network",
        "badge": "📶 店舗ネットワーク (AGENT 03)",
        "agent_id": "network_specialist",
        "agent_name": "店舗ネットワーク スペシャリスト",
        "title": "店舗バックヤードWi-Fi切断 (#11980)",
        "prompt": "西店のバックヤードでネットワークが繋がらない状態です。検品ハンディや品出しタブレットもオフラインになっています。",
        "description": "チケット#11980とSNMPトラップログからネットワークスイッチの電源供給トラブルを特定し、スイッチ再起動手順を提示します。",
    },
    {
        "id": "scenario-pos",
        "badge": "🛒 専用ゲート (AGENT 04)",
        "agent_id": "pos_specialist",
        "agent_name": "POS スペシャリスト",
        "title": "スマートカート専用ゲート通過エラー (#12345)",
        "prompt": "中央店のスマートカート専用ゲート（2番ゲート）を通過した際、ゲート通過認証が完了せず画面がフリーズしています（ERR-GATE-408）。過去に似た事象はありますか？",
        "description": "チケット#12345とゲート通信ログから無線レシーバーのセッション飽和を特定し、再起動と通過認証リトライ手順を提示します。",
    },
    {
        "id": "scenario-pos-esl",
        "badge": "🏷️ 電子棚札 (AGENT 04)",
        "agent_id": "pos_specialist",
        "agent_name": "POS スペシャリスト",
        "title": "電子棚札AI値下げ未更新 (#12110)",
        "prompt": "駅前店の惣菜売場で、電子棚札（ESL）に夕方のAI自動値下げ価格が反映されていません（ESL-SYNC-501）。復旧手順を教えてください。",
        "description": "チケット#12110からESL基地局（ESL-GW-DELI-01）の通信詰まりを特定し、基地局再起動およびマスタ差分再送手順を提示します。",
    },
]
