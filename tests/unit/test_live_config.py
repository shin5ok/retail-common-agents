"""Gemini Live API のモデル・リージョン設定の単体テスト."""

from app import agent as agent_module


def test_live_defaults_are_vertex_supported_models():
    # Vertex AI で提供される Live モデル (gemini-3.1-flash-live-preview は Developer API 専用のため不可)
    assert agent_module.LIVE_MODEL_NAME in ("gemini-3.8-live", "gemini-live-2.5-flash-native-audio")
    assert agent_module.LIVE_FALLBACK_MODEL_NAME == "gemini-live-2.5-flash-native-audio"
    assert agent_module.LIVE_API_LOCATION != "global"


def test_live_client_kwargs_pins_region_on_vertex(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")
    kwargs = agent_module.live_client_kwargs()
    assert kwargs == {
        "vertexai": True,
        "project": "test-proj",
        "location": agent_module.LIVE_API_LOCATION,
    }


def test_live_client_kwargs_none_for_developer_api(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    assert agent_module.live_client_kwargs() is None


def test_build_live_app_uses_given_model(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    live_app = agent_module.build_live_app("gemini-live-2.5-flash-native-audio")
    model = live_app.root_agent.model
    assert model.model == "gemini-live-2.5-flash-native-audio"
    assert model.client_kwargs["location"] == agent_module.LIVE_API_LOCATION
