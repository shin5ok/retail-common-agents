---
okf_version: "0.2"
type: "retail.use_case"
id: "scenario-infra"
badge: "🖥️ インフラ (AGENT 01)"
title: "在庫APサーバーCPU100% (#10892)"
domain: "infra"
agent_id: "infra_specialist"
agent_name: "インフラ スペシャリスト"
agent_label: "AGENT 01: インフラ スペシャリスト"
ticket_id: "#10892"
runbook_id: "RB-INF-001"
description: "チケット#10892とコンテナログから無限リトライによるスレッド飽和を特定し、ローリング再起動手順を提示します。"
default_prompt: "在庫・AIカメラ欠品検知APサーバー（ap-inv-prod-02）のCPU使用率が100%に張り付いて504タイムアウトが多発しています。一次対処を教えてください。"
trigger_phrases:
  - "サーバーが重い"
  - "重すぎ"
  - "サーバー落ちた"
  - "サーバ落ちてる"
  - "CPU張り付いてる"
  - "CPU100パーセント"
  - "504出てる"
  - "504エラー"
  - "タイムアウト多発"
  - "APサーバー重い"
  - "APサーバーが応答しない"
  - "ポッドが死んでる"
  - "pod再起動したい"
  - "在庫管理サーバーが遅い"
  - "AIカメラのサーバーおかしい"
  - "サーバー高負荷"
keywords:
  - "サーバー"
  - "サーバ"
  - "cpu"
  - "100%"
  - "張り付"
  - "重い"
  - "重すぎ"
  - "504"
  - "タイムアウト"
  - "gateway timeout"
  - "ap-inv"
  - "ap-inv-prod"
  - "高負荷"
  - "スレッド"
  - "pod"
  - "ポッド"
  - "gke"
  - "在庫管理"
  - "inf-cpu-100"
  - "10892"
---

# AIカメラAPサーバーCPU100% (#10892)

## 障害概要
在庫・AIカメラ欠品検知APサーバー（`ap-inv-prod-02`）において、CPU使用率が100%に張り付き、API応答が504 Gateway Timeoutとなる障害。

## 推奨一次対応
1. 該当ワーカーPod（`inventory-sync-worker`）のローリング再起動を実行
2. デッドレターキューへの滞留ジョブ退避を確認し、CPU負荷の正常化を確認
