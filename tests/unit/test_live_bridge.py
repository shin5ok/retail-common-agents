"""Live 読み上げ依頼プロンプト (build_speak_prompt) の単体テスト."""

from app.live_bridge import build_speak_prompt


def test_build_speak_prompt_contains_script_and_instruction():
    prompt = build_speak_prompt("【対応指示】\nPOS-01 を再起動してください。")
    assert prompt is not None
    assert "一字一句そのまま" in prompt
    assert "【原稿】" in prompt
    # 読み上げ用原稿変換 (POS-01 の読み仮名化) が適用されている
    assert "ピーオーエス・ゼロワン" in prompt
    assert "再起動してください" in prompt


def test_build_speak_prompt_plain_text_fallback():
    prompt = build_speak_prompt("レジの電源を確認してください。")
    assert prompt is not None
    assert "電源を確認してください" in prompt


def test_build_speak_prompt_empty_returns_none():
    assert build_speak_prompt("") is None
    assert build_speak_prompt("   \n ") is None
