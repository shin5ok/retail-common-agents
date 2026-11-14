---
okf_version: "0.2"
title: "AIOps Multi-Agent Platform Use Cases"
description: "店舗・システム運用AIエージェントのデモユースケースおよびトラブルシューティングガイドのOKFナレッジバンドル"
status: "stable"
generated:
  by: "aiops-team/1.0"
  at: "2026-09-28T23:12:00Z"
---

# AIOps Multi-Agent Platform Use Cases

店舗現場およびクラウド基盤で発生するトラブルシューティング・ユースケース一覧です。
OKF (Open Knowledge Format v0.2) 仕様に準拠し、現場の曖昧・崩れた自然言語クエリからでも迅速に一次切り分け手順へルーティングします。

## ユースケース一覧 (スライド準拠 4大スペシャリスト)

* [scenario_infra](use_cases/scenario_infra.md) - AGENT 01: インフラ スペシャリスト（在庫APサーバー CPU100% 張り付き障害 #10892）
* [scenario_order](use_cases/scenario_order.md) - AGENT 02: 発注管理 スペシャリスト（生鮮品 AI自動発注データEDI未反映障害 #11403）
* [scenario_network](use_cases/scenario_network.md) - AGENT 03: 店舗ネットワーク スペシャリスト（店舗バックヤードWi-Fi 電源供給トラブル #11980）
* [scenario_pos](use_cases/scenario_pos.md) - AGENT 04: POS スペシャリスト（スマートカート専用ゲート通過時 通過認証タイムアウト・フリーズ #12345）

### 拡張・現場機器シナリオ
* [scenario_pos_esl](use_cases/scenario_pos_esl.md) - 惣菜売場 電子棚札(ESL) 夕方AI自動値下げ未更新障害 (#12110)
* [scenario_pos_weight](use_cases/scenario_pos_weight.md) - スマートカート 重量センサー・スキャン漏れ検知頻発 (#12085)
* [scenario_camera_diagnostic](use_cases/scenario_camera_diagnostic.md) - 機器カメラ撮影・画像AIビジュアル診断 (#12350)
