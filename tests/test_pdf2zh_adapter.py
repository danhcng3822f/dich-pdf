import pytest
from unittest.mock import MagicMock, patch
import httpx

from app.services.pdf2zh_engine.adapter import (
    BaseTranslator,
    GoogleFreeTranslator,
    BingFreeTranslator,
    LLMTranslator,
    PDF2ZHAdapter,
    create_translator,
    create_adapter,
    normalize_tokens,
)
from app.services.ai_service import AIConfig


class DummyTranslator(BaseTranslator):
    def __init__(self, **kwargs):
        super().__init__(name="dummy", **kwargs)
        self.call_count = 0

    def do_translate(self, text: str) -> str:
        self.call_count += 1
        return f"[translated: {text}]"


def test_base_translator_cache_hit():
    translator = DummyTranslator(lang_in="en", lang_out="vi")
    text = "Hello world"

    # First call - translates and caches
    result1 = translator.translate(text)
    assert result1 == "[translated: Hello world]"
    assert translator.call_count == 1

    # Second call - served from cache
    result2 = translator.translate(text)
    assert result2 == "[translated: Hello world]"
    assert translator.call_count == 1


def test_base_translator_empty_and_token_bypass():
    translator = DummyTranslator(lang_in="en", lang_out="vi")

    # Empty and whitespace
    assert translator.translate("") == ""
    assert translator.translate("   ") == "   "

    # Single formula tokens bypass translation engine
    assert translator.translate("{v0}") == "{v0}"
    assert translator.translate("{v42}") == "{v42}"
    assert translator.translate("  {v1}  ") == "  {v1}  "
    assert translator.call_count == 0


def test_token_normalization():
    raw = "Công thức { v0 } và {v 1} kết hợp { v 2 } cùng {V3}."
    normalized = normalize_tokens(raw)
    assert normalized == "Công thức {v0} và {v1} kết hợp {v2} cùng {v3}."


def test_adapter_preserves_tokens():
    class MockEngine:
        def translate(self, text: str) -> str:
            return text.replace("Hello", "Xin chào")

    adapter = PDF2ZHAdapter(engine=MockEngine())
    result = adapter.translate("Hello {v0}, this is {v1}.")
    assert "{v0}" in result
    assert "{v1}" in result
    assert "Xin chào" in result


def test_adapter_batch_translate():
    class MockEngine:
        def translate(self, text: str) -> str:
            return f"trans_{text}"

    adapter = PDF2ZHAdapter(engine=MockEngine())
    results = adapter.translate_batch(["one", "{v0}", "two"])
    assert results == ["trans_one", "{v0}", "trans_two"]


def test_create_translator_factory():
    # Google free
    t_google = create_translator("google", target_lang="vi")
    assert isinstance(t_google, GoogleFreeTranslator)
    assert t_google.lang_out == "vi"

    t_google_free = create_translator("google_free", target_lang="zh-cn")
    assert isinstance(t_google_free, GoogleFreeTranslator)

    # Bing free
    t_bing = create_translator("bing", target_lang="vi")
    assert isinstance(t_bing, BingFreeTranslator)

    t_bing_free = create_translator("bing_free", target_lang="vi")
    assert isinstance(t_bing_free, BingFreeTranslator)

    # LLM providers
    t_openai = create_translator("openai", target_lang="vi", api_key="sk-test", model="gpt-4o")
    assert isinstance(t_openai, LLMTranslator)
    assert t_openai.config.provider == "openai"
    assert t_openai.config.api_key == "sk-test"
    assert t_openai.config.model == "gpt-4o"

    t_deepseek = create_translator("deepseek", target_lang="vi", api_key="sk-ds")
    assert isinstance(t_deepseek, LLMTranslator)
    assert t_deepseek.config.provider == "deepseek"


def test_create_adapter_factory():
    adapter = create_adapter("google", target_lang="vi")
    assert isinstance(adapter, GoogleFreeTranslator)

    adapter_llm = create_adapter("gemini", api_key="gem-key", target_lang="vi")
    assert isinstance(adapter_llm, LLMTranslator)


def test_google_free_translator_mocked():
    html_response = (
        '<!DOCTYPE html><html><body>'
        '<div class="result-container">Công thức {v0} và {v1}.</div>'
        '</body></html>'
    )
    with patch.object(httpx.Client, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_response
        mock_get.return_value = mock_resp

        translator = GoogleFreeTranslator(lang_in="en", lang_out="vi")
        out = translator.translate("Formula {v0} and {v1}.")
        assert "{v0}" in out
        assert "{v1}" in out
        assert "Công thức" in out
        mock_get.assert_called_once()


def test_google_free_translator_live():
    translator = GoogleFreeTranslator(lang_in="en", lang_out="vi")
    try:
        translated = translator.translate("Deep learning with {v0} is effective.")
        assert "{v0}" in translated
        assert len(translated) > 5
    except Exception as exc:
        pytest.skip(f"Google live translation skipped due to network/rate-limit: {exc}")



def test_bing_free_translator_mocked():
    with patch.object(BingFreeTranslator, "find_sid", return_value=("https://www.bing.com/", "ig123", "iid456", "k1", "t1")), \
         patch.object(httpx.Client, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"translations": [{"text": "Dịch từ Bing {v0}"}]}]
        mock_post.return_value = mock_resp

        translator = BingFreeTranslator(lang_in="en", lang_out="vi")
        res = translator.translate("Text with {v0}")
        assert res == "Dịch từ Bing {v0}"


def test_bing_free_translator_fallback_to_google():
    with patch.object(BingFreeTranslator, "find_sid", side_effect=Exception("Bing blocked")), \
         patch.object(GoogleFreeTranslator, "do_translate", return_value="Fallback translation {v0}"):
        translator = BingFreeTranslator(lang_in="en", lang_out="vi")
        res = translator.translate("Text with {v0}")
        assert res == "Fallback translation {v0}"


def test_llm_translator_openai_mocked():
    config = AIConfig(
        provider="openai",
        api_key="test-api-key",
        model="gpt-4o-mini",
        temperature=0.2,
    )
    translator = LLMTranslator(config=config, lang_in="en", lang_out="vi")

    with patch.object(httpx.Client, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Văn bản dịch chứa {v0}."}}]
        }
        mock_post.return_value = mock_resp

        result = translator.translate("Input text with {v0}.")
        assert result == "Văn bản dịch chứa {v0}."

        # Verify prompt rules were included
        call_kwargs = mock_post.call_args.kwargs
        payload = call_kwargs["json"]
        system_msg = payload["messages"][0]["content"]
        assert "{v\\d+}" in system_msg or "{v" in system_msg
        assert "preserve" in system_msg.lower()


def test_llm_translator_gemini_mocked():
    config = AIConfig(
        provider="gemini",
        api_key="test-gemini-key",
        model="gemini-1.5-flash",
    )
    translator = LLMTranslator(config=config, lang_in="en", lang_out="vi")

    with patch.object(httpx.Client, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Gemini dịch {v0}"}]}}]
        }
        mock_post.return_value = mock_resp

        result = translator.translate("Translate {v0}")
        assert result == "Gemini dịch {v0}"
        call_url = mock_post.call_args[0][0]
        assert "generativelanguage.googleapis.com" in call_url
        assert "test-gemini-key" in call_url


def test_llm_translator_claude_mocked():
    config = AIConfig(
        provider="claude",
        api_key="test-claude-key",
        model="claude-3-5-sonnet-20241022",
    )
    translator = LLMTranslator(config=config, lang_in="en", lang_out="vi")

    with patch.object(httpx.Client, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [{"text": "Claude dịch {v0}"}]
        }
        mock_post.return_value = mock_resp

        result = translator.translate("Translate {v0}")
        assert result == "Claude dịch {v0}"
        call_headers = mock_post.call_args.kwargs["headers"]
        assert call_headers["x-api-key"] == "test-claude-key"


def test_llm_translator_custom_requires_base_url():
    config = AIConfig(
        provider="custom",
        api_key="key",
        base_url=None,
    )
    translator = LLMTranslator(config=config, lang_in="en", lang_out="vi")
    with pytest.raises(ValueError, match="Custom provider requires Base URL"):
        translator.translate("Hello")
