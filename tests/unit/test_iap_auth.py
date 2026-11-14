"""Cloud Run Native IAP 認可ミドルウェアの単体テスト."""

import os
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.fast_api_app import app

client = TestClient(app)


def test_iap_bypass_when_not_in_cloud_run_or_not_forced():
    """Cloud Run 環境外かつ強制フラグ未設定の場合、ミドルウェアがバイパスすることを確認."""
    with patch.dict(os.environ, {"K_SERVICE": "", "ENABLE_IAP_CHECK": "", "IAP_USERS": "admin@example.com"}, clear=False):
        response = client.get("/api/scenarios")
        assert response.status_code == 200


def test_iap_bypass_when_iap_users_not_configured():
    """Cloud Run 上でも IAP_USERS が未設定の場合、全エンドポイントがミドルウェアをバイパスすることを確認."""
    with patch.dict(os.environ, {"K_SERVICE": "aiops-multi-agent", "IAP_USERS": ""}, clear=False):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        response_api = client.get("/api/scenarios")
        assert response_api.status_code == 200


def test_iap_health_check_always_bypassed():
    """IAP_USERS が設定されていても /health は常に認可不要でアクセス可能なことを確認."""
    with patch.dict(os.environ, {"ENABLE_IAP_CHECK": "true", "IAP_USERS": "allowed@example.com"}, clear=False):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_iap_authorized_user_access():
    """IAP_USERS に登録されているユーザーは X-Goog-Authenticated-User-Email ヘッダー経由でアクセス可能なことを確認."""
    with patch.dict(
        os.environ,
        {"ENABLE_IAP_CHECK": "true", "IAP_USERS": "alice@example.com, bob@example.com"},
        clear=False,
    ):
        headers = {
            "X-Goog-Authenticated-User-Email": "accounts.google.com:alice@example.com"
        }
        response = client.get("/api/scenarios", headers=headers)
        assert response.status_code == 200

        # 大文字小文字混在のケース
        headers_upper = {
            "X-Goog-Authenticated-User-Email": "accounts.google.com:BOB@EXAMPLE.COM"
        }
        response_upper = client.get("/api/scenarios", headers=headers_upper)
        assert response_upper.status_code == 200


def test_iap_unauthorized_user_forbidden():
    """IAP_USERS に登録されていないユーザーは 403 Forbidden となることを確認."""
    with patch.dict(os.environ, {"ENABLE_IAP_CHECK": "true", "IAP_USERS": "alice@example.com"}, clear=False):
        headers = {
            "X-Goog-Authenticated-User-Email": "accounts.google.com:charlie@example.com"
        }
        response = client.get("/api/scenarios", headers=headers)
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "Forbidden"
        assert "charlie@example.com" in data["message"]


def test_iap_missing_header_forbidden():
    """IAP_USERS が設定されている環境でヘッダーが存在しない場合は 403 Forbidden となることを確認."""
    with patch.dict(os.environ, {"ENABLE_IAP_CHECK": "true", "IAP_USERS": "alice@example.com"}, clear=False):
        response = client.get("/api/scenarios")
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "Forbidden"
        assert "anonymous" in data["message"]
