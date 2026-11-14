"""ADK 2.x 専門エージェント用 FunctionTools.

ADK ツール規約に準拠:
- 明確な docstring（LLM が参照）
- すべての引数に型ヒントを指定（デフォルト値は持たせない）
- `tool_context: ToolContext` を用いてセッション state に推論ステップ（Chain of Thought）と
  RAG 検索結果（Grounding データ）を記録
- 戻り値は JSON シリアライズ可能な dict
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from app.rag_store import get_runbook, search_logs, search_tickets


def _append_reasoning_step(
    tool_context: ToolContext | None,
    step_type: str,
    agent_name: str,
    title: str,
    detail: str,
    evidence: list[dict[str, Any]] | None = None,
) -> None:
    """セッション state に可視化用の推論プロセス（Chain of Thought）を記録する."""
    if tool_context is None:
        return
    steps: list[dict[str, Any]] = list(tool_context.state.get("reasoning_steps", []))
    steps.append(
        {
            "step_no": len(steps) + 1,
            "type": step_type,
            "agent": agent_name,
            "title": title,
            "detail": detail,
            "evidence": evidence or [],
        }
    )
    tool_context.state["reasoning_steps"] = steps


def search_incident_tickets(
    query: str,
    domain: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """BigQuery 上の過去インシデント対応チケットをベクトル・キーワード検索し、類似事例を抽出します。

    Args:
        query: 障害内容や検索キーワード（例: '中央店 スマートカート ゲート 通過認証 タイムアウト'）。
        domain: 対象ドメイン（'pos', 'infra', 'order', 'network' のいずれか）。

    Returns:
        類似チケット一覧と検索ステータスを含む辞書。
    """
    normalized_domain = domain.lower().strip()
    if normalized_domain not in {"pos", "infra", "order", "network"}:
        normalized_domain = "pos"

    tickets = search_tickets(query=query, domain=normalized_domain, top_k=2)
    tool_context.state["rag_tickets"] = tickets
    tool_context.state["active_domain"] = normalized_domain

    top_ticket = tickets[0] if tickets else {}
    _append_reasoning_step(
        tool_context=tool_context,
        step_type="rag_ticket_search",
        agent_name=f"{normalized_domain}_specialist",
        title=f"過去インシデントチケット検索 (BigQuery Vector Search: {normalized_domain.upper()})",
        detail=(
            f"クエリ「{query}」で類似チケットを検索し、"
            f"チケット {top_ticket.get('ticket_id', 'N/A')}「{top_ticket.get('title', '')}」"
            f"（類似度: {top_ticket.get('similarity_score', 0.0)}）を抽出しました。"
        ),
        evidence=tickets,
    )

    return {
        "status": "success",
        "domain": normalized_domain,
        "matched_count": len(tickets),
        "tickets": tickets,
    }


def search_splunk_logs(
    query: str,
    domain: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """BigQuery に集約された Splunk システムエラーログから直近の異常ログを検索・抽出します。

    Args:
        query: 検索対象の店舗名・ホスト名・エラー事象（例: '中央店 専用ゲート タイムアウト'）。
        domain: 対象ドメイン（'pos', 'infra', 'order', 'network' のいずれか）。

    Returns:
        抽出された Splunk エラーログ一覧を含む辞書。
    """
    normalized_domain = domain.lower().strip()
    if normalized_domain not in {"pos", "infra", "order", "network"}:
        normalized_domain = "pos"

    logs = search_logs(query=query, domain=normalized_domain, top_k=2)
    tool_context.state["rag_logs"] = logs

    top_log = logs[0] if logs else {}
    _append_reasoning_step(
        tool_context=tool_context,
        step_type="rag_log_search",
        agent_name=f"{normalized_domain}_specialist",
        title=f"Splunk 集約エラーログ検索 ({top_log.get('error_code', 'LOG')})",
        detail=(
            f"ホスト `{top_log.get('host', 'unknown')}` からエラーコード "
            f"`{top_log.get('error_code', 'N/A')}` を検出: {top_log.get('message', '')}"
        ),
        evidence=logs,
    )

    return {
        "status": "success",
        "domain": normalized_domain,
        "log_count": len(logs),
        "logs": logs,
    }


def get_recovery_runbook(
    domain: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """対象システム領域の標準一次切り分け・復旧手順書（Runbook）とエスカレーション基準を取得します。

    Args:
        domain: 対象ドメイン（'pos', 'infra', 'order', 'network' のいずれか）。

    Returns:
        復旧手順ステップと有人エスカレーション窓口情報を含む辞書。
    """
    normalized_domain = domain.lower().strip()
    if normalized_domain not in {"pos", "infra", "order", "network"}:
        normalized_domain = "pos"

    runbook = get_runbook(normalized_domain)
    tool_context.state["runbook"] = runbook

    _append_reasoning_step(
        tool_context=tool_context,
        step_type="runbook_lookup",
        agent_name=f"{normalized_domain}_specialist",
        title=f"標準復旧手順書 (Runbook: {runbook.get('runbook_id')}) の照合",
        detail=f"手順書「{runbook.get('title')}」から一次切り分け手順（全{len(runbook.get('steps', []))}ステップ）とエスカレーション基準を取得しました。",
        evidence=[runbook],
    )

    return {
        "status": "success",
        "runbook": runbook,
    }


def evaluate_escalation(
    domain: str,
    issue_summary: str,
    confidence_level: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """一次切り分け手順で解決しない場合や確信度に応じた有人オペレーターへのエスカレーション判定を行います。

    Args:
        domain: 対象ドメイン（'pos', 'infra', 'order', 'network' のいずれか）。
        issue_summary: 発生している事象の要約。
        confidence_level: 解決策の確信度（'high', 'medium', 'low' のいずれか）。

    Returns:
        エスカレーション推奨状態、担当窓口、確認メッセージを含む辞書。
    """
    normalized_domain = domain.lower().strip()
    runbook = get_runbook(normalized_domain)
    conf = confidence_level.lower().strip()
    immediate_human_required = conf == "low"

    escalation_info = {
        "domain": normalized_domain,
        "issue_summary": issue_summary,
        "confidence_level": conf,
        "immediate_human_required": immediate_human_required,
        "escalation_team": runbook.get("escalation_team", "AIOps 有人総合サポートデスク"),
        "escalation_criteria": runbook.get("escalation_criteria", ""),
        "prompt_to_user": "解決しない場合は有人サポートへエスカレーションしますか？",
    }
    tool_context.state["escalation"] = escalation_info

    _append_reasoning_step(
        tool_context=tool_context,
        step_type="escalation_check",
        agent_name="aiops_orchestrator",
        title="エスカレーション判定 & 回答統合",
        detail=(
            f"解決確信度: {conf.upper()} — "
            f"{'即時有人エスカレーションを推奨' if immediate_human_required else '一次切り分け手順を提示し、未解決時の有人サポート窓口（' + escalation_info['escalation_team'] + '）へのエスカレーション確認を付与'}。"
        ),
        evidence=[escalation_info],
    )

    return {
        "status": "success",
        "escalation": escalation_info,
    }
