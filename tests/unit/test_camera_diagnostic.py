"""カメラ撮影・画像AIビジュアル診断機能の単体テスト."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent import run_aiops_pipeline
from app.fast_api_app import app
from app.okf_manager import get_okf_manager


@pytest.fixture
def client():
    return TestClient(app)


def test_okf_camera_diagnostic_scenario_loaded():
    """OKF からカメラビジュアル診断ユースケースが正しくロードされること."""
    mgr = get_okf_manager()
    cam_uc = mgr.get_use_case("scenario-camera-diagnostic")
    assert cam_uc is not None, "scenario-camera-diagnostic がロードされていません"
    assert cam_uc.ticket_id == "#12350"
    assert cam_uc.domain == "pos"
    assert "カメラ" in cam_uc.keywords

    # 自然言語・適当な文章からのマッチング検証
    test_queries = [
        "写真撮ったから見て",
        "カメラで見て",
        "エラー画面撮った",
        "ランプ赤点滅してる",
        "写真で診断して",
    ]
    for q in test_queries:
        matched, score = mgr.match_use_case(q)
        assert matched is not None, f"クエリ '{q}' がマッチしませんでした"
        assert matched.id == "scenario-camera-diagnostic", f"クエリ '{q}' が別のユースケースにマッチ: {matched.id}"
        assert score >= 0.85, f"スコアが低すぎます: {score}"


def test_run_aiops_pipeline_with_image_data():
    """画像データ添付時にカメラビジュアル解析ステップおよび回答が正しく生成されること."""
    dummy_image = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
    
    result = run_aiops_pipeline(
        query="この写真のランプ状態を確認してください",
        session_id="test-camera-session-1",
        image_data=dummy_image,
    )

    assert result["status"] == "success"
    assert result["image_data"] == dummy_image
    assert result["domain"] == "pos"
    assert result["assigned_agent"] == "pos_specialist"

    # 推論ステップの先頭がカメラ画像マルチモーダル解析であること
    steps = result["reasoning_steps"]
    assert len(steps) >= 6
    visual_step = steps[0]
    assert visual_step["type"] == "visual_multimodal_analysis"
    assert "カメラ画像AI" in visual_step["title"]
    assert visual_step["evidence"][0]["has_image"] is True
    assert "GATE-AP-02" in visual_step["evidence"][0]["detected_device"]

    # 回答テキストにビジュアル解析結果と復旧手順が含まれること
    assert "カメラ画像AIビジュアル解析結果" in result["answer"]
    assert "GATE-AP-02" in result["answer"]
    assert "再起動" in result["answer"]


def test_chat_endpoint_with_image_data(client):
    """POST /api/chat が image_data を受け取り、マルチモーダル推論結果を返すこと."""
    dummy_image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    
    response = client.post(
        "/api/chat",
        json={
            "query": "【現場写真添付】画面が止まっています",
            "session_id": "api-camera-test",
            "image_data": dummy_image,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["image_data"] == dummy_image
    assert "カメラ画像AIビジュアル解析結果" in data["answer"]
    assert len(data["reasoning_steps"]) >= 6


def test_scenarios_endpoint_includes_camera_diagnostic(client):
    """GET /api/scenarios はスライド準拠の4大スペシャリストを返し、OKF内部でカメラ診断も保持していること."""
    from app.okf_manager import get_okf_manager

    mgr = get_okf_manager()
    cam_uc = mgr.get_use_case("scenario-camera-diagnostic")
    assert cam_uc is not None
    assert "ビジュアル診断" in cam_uc.badge

    response = client.get("/api/scenarios")
    assert response.status_code == 200
    scenarios = response.json()
    assert len(scenarios) >= 4


_IMG = "data:image/png;base64,iVBORw0KGgo="


@pytest.mark.parametrize(
    "first_query, image_query",
    [
        ("バックヤードのWi-Fiがつながりません", "これを見てください"),  # ネットワーク相談の途中で写真
        ("サーバーのCPUが高い", "サーバーの写真です"),  # インフラ語を含む写真
    ],
)
def test_image_upload_always_routes_to_pos_camera_diagnostic(first_query, image_query):
    """写真アップロード時は直前の担当や本文に関係なく POS のカメラ画像診断に固定されること."""
    sess = f"img-fixed-{hash(first_query)}"
    run_aiops_pipeline(query=first_query, session_id=sess)
    res = run_aiops_pipeline(query=image_query, session_id=sess, image_data=_IMG)
    assert res["assigned_agent"] == "pos_specialist"
    assert "カメラ画像AIビジュアル解析結果" in res["answer"]
