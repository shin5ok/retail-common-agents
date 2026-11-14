"""OKF (Open Knowledge Format v0.2) ユースケース管理および高精度マッチャー.

現場スタッフの多様な入力（適当な短文、崩れた口語表現、曖昧な言い回しなど）から
okf/use_cases/ 配下の OKF Markdown 定義を照合し、最適なユースケース・ドメイン・エージェント・
過去チケットを特定します。
"""

from __future__ import annotations

import difflib
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# プロジェクトルートの okf/ ディレクトリ
BASE_DIR = Path(__file__).resolve().parent.parent
OKF_DIR = BASE_DIR / "okf"
USE_CASES_DIR = OKF_DIR / "use_cases"


@dataclass
class OKFUseCase:
    """OKF v0.2 形式で記述されたトラブルシューティング・ユースケース."""

    id: str
    badge: str
    title: str
    domain: str
    agent_id: str
    agent_name: str
    agent_label: str
    ticket_id: str
    runbook_id: str
    description: str
    default_prompt: str
    trigger_phrases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    markdown_body: str = ""

    def to_scenario_dict(self) -> dict[str, Any]:
        """Web UI /api/scenarios 用の辞書表現を生成."""
        return {
            "id": self.id,
            "badge": self.badge,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "title": self.title,
            "prompt": self.default_prompt,
            "description": self.description,
            "ticket_id": self.ticket_id,
            "domain": self.domain,
        }


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """YAML フロントマターと Markdown 本文をパースする（yaml ライブラリ非依存の堅牢パーサー併装）."""
    frontmatter_dict: dict[str, Any] = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_yaml = parts[1]
            body = parts[2].strip()

            try:
                import yaml

                loaded = yaml.safe_load(raw_yaml)
                if isinstance(loaded, dict):
                    frontmatter_dict = loaded
            except Exception:
                # YAML パッケージがない場合のフォールバック簡易パーサー
                current_list_key = None
                for line in raw_yaml.splitlines():
                    line_s = line.strip()
                    if not line_s or line_s.startswith("#"):
                        continue
                    if line_s.startswith("- ") and current_list_key:
                        val = line_s[2:].strip().strip('"').strip("'")
                        frontmatter_dict.setdefault(current_list_key, []).append(val)
                    elif ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if not v:
                            current_list_key = k
                            frontmatter_dict[k] = []
                        else:
                            current_list_key = None
                            frontmatter_dict[k] = v

    return frontmatter_dict, body


class OKFManager:
    """OKF バンドルのロード、キャッシュ、およびファジーマッチングを統括するマネージャー."""

    def __init__(self, use_cases_dir: Path = USE_CASES_DIR) -> None:
        self.use_cases_dir = use_cases_dir
        self._use_cases: dict[str, OKFUseCase] = {}
        self.load_all()

    def load_all(self) -> None:
        """use_cases ディレクトリ内の全 OKF Markdown をロード."""
        if not self.use_cases_dir.exists():
            return

        for file_path in self.use_cases_dir.glob("*.md"):
            try:
                content = file_path.read_text(encoding="utf-8")
                fm, body = _parse_frontmatter(content)
                if not fm or "id" not in fm:
                    continue

                use_case = OKFUseCase(
                    id=str(fm.get("id", file_path.stem)),
                    badge=str(fm.get("badge", "📌 ユースケース")),
                    title=str(fm.get("title", file_path.stem)),
                    domain=str(fm.get("domain", "pos")),
                    agent_id=str(fm.get("agent_id", "pos_specialist")),
                    agent_name=str(fm.get("agent_name", "AI専門スタッフ")),
                    agent_label=str(
                        fm.get("agent_label", "AGENT: AI専門スタッフ")
                    ),
                    ticket_id=str(fm.get("ticket_id", "#00000")),
                    runbook_id=str(fm.get("runbook_id", "RB-000")),
                    description=str(fm.get("description", "")),
                    default_prompt=str(fm.get("default_prompt", "")),
                    trigger_phrases=list(fm.get("trigger_phrases", [])),
                    keywords=list(fm.get("keywords", [])),
                    markdown_body=body,
                )
                self._use_cases[use_case.id] = use_case
            except Exception as e:
                print(f"[OKFManager] Error loading {file_path}: {e}")

    def get_all_use_cases(self) -> list[OKFUseCase]:
        """ロードされた全ユースケースを取得."""
        return list(self._use_cases.values())

    def get_use_case(self, uc_id: str) -> OKFUseCase | None:
        """ID を指定して特定のユースケースを取得."""
        return self._use_cases.get(uc_id)

    def get_scenarios(self) -> list[dict[str, Any]]:
        """Web UI 用のデモシナリオ一覧 (スライド準拠の4大スペシャリストを順序固定で返却)."""
        target_order = [
            "scenario-infra",
            "scenario-order",
            "scenario-network",
            "scenario-pos",
            "scenario-pos-esl",
        ]
        result = []
        for sid in target_order:
            if sid in self._use_cases:
                result.append(self._use_cases[sid].to_scenario_dict())
        if result:
            return result
        return [uc.to_scenario_dict() for uc in self._use_cases.values()]

    def match_use_case(self, query: str) -> tuple[OKFUseCase | None, float]:
        """現場スタッフの『適当な文章』から最適な OKF ユースケースを高確信度でマッチング.

        マッチングロジック:
        1. trigger_phrases との直接部分一致 / 含有判定 (高優先ブースト)
        2. 文字列類似度 (difflib SequenceMatcher)
        3. keywords との加重合致数
        4. 総合スコア（0.0 〜 1.0）の算出
        """
        q = query.lower().strip()
        if not q or not self._use_cases:
            return None, 0.0

        best_use_case: OKFUseCase | None = None
        best_score = 0.0

        for uc in self._use_cases.values():
            score = 0.0

            # 1. trigger_phrases マッチング
            for phrase in uc.trigger_phrases:
                p_lower = phrase.lower()
                # 相互部分一致（フレーズがクエリに含まれる、またはクエリがフレーズに含まれる）
                if p_lower in q:
                    score = max(score, 0.95)
                elif len(q) >= 3 and q in p_lower:
                    score = max(score, 0.90)
                else:
                    # 短文同士のレーベンシュタイン風類似度
                    sim = difflib.SequenceMatcher(None, q, p_lower).ratio()
                    if sim > 0.65:
                        score = max(score, 0.70 + sim * 0.25)

            # 2. keywords マッチング
            matched_kw_count = 0
            for kw in uc.keywords:
                if kw.lower() in q:
                    matched_kw_count += 1

            if matched_kw_count > 0:
                kw_score = min(0.92, 0.60 + matched_kw_count * 0.10)
                score = max(score, kw_score)

            # 3. タイトル・説明文の部分一致
            if uc.title.lower() in q or (len(q) >= 4 and q in uc.title.lower()):
                score = max(score, 0.88)

            if score > best_score:
                best_score = score
                best_use_case = uc

        return best_use_case, round(best_score, 3)


# シングルトンインスタンス
_GLOBAL_OKF_MANAGER: OKFManager | None = None


def get_okf_manager() -> OKFManager:
    """OKFManager のシングルトンを取得."""
    global _GLOBAL_OKF_MANAGER
    if _GLOBAL_OKF_MANAGER is None:
        _GLOBAL_OKF_MANAGER = OKFManager()
    return _GLOBAL_OKF_MANAGER
