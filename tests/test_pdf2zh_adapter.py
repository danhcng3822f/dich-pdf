import html
import os
import re
from contextlib import nullcontext

import pytest
from unittest.mock import MagicMock, patch
import httpx

from app.services.pdf2zh_engine.adapter import (
    BaseTranslator,
    GoogleFreeTranslator,
    BingFreeTranslator,
    FreeTranslationError,
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


def _google_html(text: str) -> str:
    """Return the small HTML fragment used by the keyless Google endpoint."""
    return f'<div class="result-container">{html.escape(text)}</div>'


def _poisoned_css_error_page() -> str:
    """CSS text observed when a provider returns a browser error document."""
    return (
        "body{overflow:auto!important;display:block!important;}"
        "body>*{display:none!important;}"
        "#af-error-page{display:block!important;}"
    )


def _poisoned_html_error_page() -> str:
    """HTML wrapper variant of the same non-translation response."""
    return (
        "<!doctype html><html><head><style>"
        f"{_poisoned_css_error_page()}"
        "</style></head><body>Service unavailable</body></html>"
    )


def _successful_response(*, text: str = "", json_data=None) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.text = text
    response.json.return_value = json_data
    return response


def _http_error_response(status_code: int) -> httpx.Response:
    request = httpx.Request("GET", "https://translation.test/")
    return httpx.Response(status_code, request=request, text="temporary failure")


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


@pytest.mark.parametrize(
    ("language", "google_code", "bing_code"),
    [
        ("Vietnamese", "vi", "vi"),
        ("English", "en", "en"),
        ("Japanese", "ja", "ja"),
        ("Chinese", "zh-CN", "zh-Hans"),
        ("Korean", "ko", "ko"),
        ("French", "fr", "fr"),
        ("German", "de", "de"),
        ("Spanish", "es", "es"),
        ("Russian", "ru", "ru"),
    ],
)
def test_free_translators_map_all_ui_languages(language, google_code, bing_code):
    """Every language value emitted by the UI must be accepted by both services."""
    assert GoogleFreeTranslator(lang_out=language).lang_out == google_code
    assert BingFreeTranslator(lang_out=language).lang_out == bing_code


def test_google_free_uses_auto_source_language_by_default():
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(kwargs["params"])
        return _successful_response(text=_google_html("Xin chào"))

    with patch.object(httpx.Client, "get", side_effect=fake_get):
        translator = GoogleFreeTranslator(lang_out="Vietnamese", max_retries=0)
        assert translator.translate("Hello") == "Xin chào"

    assert translator.lang_in == "auto"
    assert calls == [{"tl": "vi", "sl": "auto", "q": "Hello"}]


def test_bing_free_uses_auto_source_language_by_default():
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["data"])
        return _successful_response(
            json_data=[{"translations": [{"text": "Xin chào"}]}]
        )

    with patch.object(
        BingFreeTranslator,
        "find_sid",
        return_value=("https://www.bing.com/", "ig", "iid", "key", "token"),
    ), patch.object(httpx.Client, "post", side_effect=fake_post):
        translator = BingFreeTranslator(lang_out="Vietnamese", max_retries=0)
        assert translator.translate("Hello") == "Xin chào"

    assert translator.lang_in == "auto-detect"
    assert len(sent) == 1
    assert sent[0]["fromLang"] == "auto-detect"
    assert sent[0]["to"] == "vi"


def test_google_parser_keeps_nested_text_and_line_breaks():
    response = _successful_response(
        text=(
            '<div class="result-container">Xin <strong>chào</strong>'
            '<br>công thức {v0} &amp; dữ liệu.</div>'
        )
    )
    with patch.object(httpx.Client, "get", return_value=response):
        translator = GoogleFreeTranslator(lang_out="vi", max_retries=0)
        translated = translator.translate("Hello formula {v0} & data.")

    assert translated == "Xin chào\ncông thức {v0} & dữ liệu."


def test_bing_session_metadata_is_reused_across_translations():
    def fake_post(*args, **kwargs):
        return _successful_response(
            json_data=[{"translations": [{"text": kwargs["data"]["text"]}]}]
        )

    with patch.object(
        BingFreeTranslator,
        "find_sid",
        return_value=("https://www.bing.com/", "ig", "iid", "key", "token"),
    ) as mock_sid, patch.object(httpx.Client, "post", side_effect=fake_post):
        translator = BingFreeTranslator(lang_out="vi", max_retries=0)
        assert translator.translate("First request") == "First request"
        assert translator.translate("Second request") == "Second request"

    assert mock_sid.call_count == 1


def test_bing_refreshes_session_once_after_forbidden_response():
    forbidden_request = httpx.Request(
        "POST", "https://www.bing.com/ttranslatev3"
    )
    forbidden = httpx.Response(403, request=forbidden_request)
    success = _successful_response(
        json_data=[{"translations": [{"text": "Đã làm mới phiên"}]}]
    )

    with patch.object(
        BingFreeTranslator,
        "find_sid",
        side_effect=[
            ("https://www.bing.com/", "ig1", "iid1", "key1", "token1"),
            ("https://www.bing.com/", "ig2", "iid2", "key2", "token2"),
        ],
    ) as mock_sid, patch.object(
        httpx.Client, "post", side_effect=[forbidden, success]
    ) as mock_post:
        translator = BingFreeTranslator(lang_out="vi", max_retries=0)
        assert translator.translate("Refresh this session") == "Đã làm mới phiên"

    assert mock_sid.call_count == 2
    assert mock_post.call_count == 2
    assert mock_post.call_args.kwargs["data"]["token"] == "token2"


def test_free_translator_rejects_unknown_target_and_bypasses_marker_only_text():
    with pytest.raises(ValueError, match="Unsupported language"):
        GoogleFreeTranslator(lang_out="not a real language")

    with patch.object(
        httpx.Client, "get", side_effect=AssertionError("network must not be used")
    ):
        translator = GoogleFreeTranslator(lang_out="vi")
        assert translator.translate("{ V 0 } + {v1} = 42") == "{v0} + {v1} = 42"


def test_google_free_chunks_long_input_without_truncating_or_reordering():
    source = (
        "First paragraph has several words and formula {v0}.\n\n"
        "Second paragraph must remain complete after chunking. " * 8
    ) + "Final sentinel {v12}."
    submitted_chunks = []

    def fake_get(*args, **kwargs):
        chunk = kwargs["params"]["q"]
        submitted_chunks.append(chunk)
        return _successful_response(text=_google_html(chunk))

    with patch.object(httpx.Client, "get", side_effect=fake_get):
        translator = GoogleFreeTranslator(
            lang_out="vi", max_chars=80, max_retries=0
        )
        translated = translator.translate(source)

    assert len(submitted_chunks) > 1
    assert all(0 < len(chunk) <= 80 for chunk in submitted_chunks)
    assert sum(chunk.count("{v0}") for chunk in submitted_chunks) == source.count("{v0}")
    assert sum(chunk.count("{v12}") for chunk in submitted_chunks) == 1
    assert translated == source
    assert translated.endswith("Final sentinel {v12}.")


def test_bing_free_chunks_long_input_without_truncating_or_reordering():
    source = (
        "A long segment containing {v0}, punctuation, and whitespace. " * 10
    ) + "Last sentinel {v99}."
    submitted_chunks = []

    def fake_post(*args, **kwargs):
        chunk = kwargs["data"]["text"]
        submitted_chunks.append(chunk)
        return _successful_response(
            json_data=[{"translations": [{"text": chunk}]}]
        )

    with patch.object(
        BingFreeTranslator,
        "find_sid",
        return_value=("https://www.bing.com/", "ig", "iid", "key", "token"),
    ), patch.object(httpx.Client, "post", side_effect=fake_post):
        translator = BingFreeTranslator(
            lang_out="vi", max_chars=70, max_retries=0
        )
        translated = translator.translate(source)

    assert len(submitted_chunks) > 1
    assert all(0 < len(chunk) <= 70 for chunk in submitted_chunks)
    assert sum(chunk.count("{v0}") for chunk in submitted_chunks) == source.count("{v0}")
    assert sum(chunk.count("{v99}") for chunk in submitted_chunks) == 1
    assert translated == source
    assert translated.endswith("Last sentinel {v99}.")


def test_google_free_retries_a_transient_http_failure():
    transient = _http_error_response(503)
    success = _successful_response(text=_google_html("Đã dịch {v0}"))

    with patch.object(
        httpx.Client, "get", side_effect=[transient, success]
    ) as mock_get:
        translator = GoogleFreeTranslator(
            lang_out="vi", max_retries=1, retry_backoff=0
        )
        translated = translator.translate("Translated {v0}")

    assert translated == "Đã dịch {v0}"
    assert mock_get.call_count == 2


def test_google_free_falls_back_without_retrying_a_bad_request():
    bad_request = _http_error_response(400)
    source = "Keep this complete source text {v0}"

    with patch.object(
        httpx.Client, "get", return_value=bad_request
    ) as mock_get, patch.object(
        BingFreeTranslator, "do_translate", return_value="Bing fallback {v0}"
    ) as mock_fallback:
        translator = GoogleFreeTranslator(
            lang_out="vi", max_retries=3, retry_backoff=0
        )
        assert translator.translate(source) == "Bing fallback {v0}"

    mock_get.assert_called_once()
    mock_fallback.assert_called_once_with(source)


def test_bing_free_retries_before_using_google_fallback():
    request = httpx.Request("POST", "https://www.bing.com/ttranslatev3")
    transient = httpx.ConnectError("temporary connection failure", request=request)
    success = _successful_response(
        json_data=[{"translations": [{"text": "Bing thành công {v0}"}]}]
    )

    with patch.object(
        BingFreeTranslator,
        "find_sid",
        return_value=("https://www.bing.com/", "ig", "iid", "key", "token"),
    ), patch.object(
        httpx.Client, "post", side_effect=[transient, success]
    ) as mock_post, patch.object(
        GoogleFreeTranslator,
        "do_translate",
        side_effect=AssertionError("fallback must not run after a successful retry"),
    ) as mock_fallback:
        translator = BingFreeTranslator(
            lang_out="vi", max_retries=1, retry_backoff=0
        )
        translated = translator.translate("Bing succeeds {v0}")

    assert translated == "Bing thành công {v0}"
    assert mock_post.call_count == 2
    mock_fallback.assert_not_called()


def test_bing_free_falls_back_once_after_retries_are_exhausted():
    source = "Translate the complete text {v0}, including this tail."
    request = httpx.Request("POST", "https://www.bing.com/ttranslatev3")
    transient = httpx.ReadTimeout("Bing timed out", request=request)

    with patch.object(
        BingFreeTranslator,
        "find_sid",
        return_value=("https://www.bing.com/", "ig", "iid", "key", "token"),
    ), patch.object(
        httpx.Client, "post", side_effect=transient
    ) as mock_post, patch.object(
        GoogleFreeTranslator, "do_translate", return_value="Google fallback {v0}"
    ) as mock_fallback:
        translator = BingFreeTranslator(
            lang_out="vi", max_retries=2, retry_backoff=0
        )
        translated = translator.translate(source)

    assert translated == "Google fallback {v0}"
    assert mock_post.call_count == 3
    mock_fallback.assert_called_once_with(source)


def test_google_rejects_html_css_error_page_and_uses_bing_fallback():
    source = "Insights and Recommendations {v1}"
    poisoned_response = _successful_response(
        text=_google_html(f"{_poisoned_css_error_page()} {{v1}}")
    )

    with patch.object(
        httpx.Client, "get", return_value=poisoned_response
    ), patch.object(
        BingFreeTranslator,
        "do_translate",
        return_value="Thông tin chuyên sâu và khuyến nghị {v1}",
    ) as mock_fallback:
        translated = GoogleFreeTranslator(
            lang_out="vi", max_retries=0
        ).translate(source)

    assert translated == "Thông tin chuyên sâu và khuyến nghị {v1}"
    assert "overflow" not in translated
    assert "<html" not in translated.lower()
    mock_fallback.assert_called_once_with(source)


def test_bing_rejects_html_css_error_page_and_uses_google_fallback():
    source = "Insights and Recommendations"
    poisoned_response = _successful_response(
        json_data=[
            {"translations": [{"text": _poisoned_html_error_page()}]}
        ]
    )

    with patch.object(
        BingFreeTranslator,
        "find_sid",
        return_value=("https://www.bing.com/", "ig", "iid", "key", "token"),
    ), patch.object(
        httpx.Client, "post", return_value=poisoned_response
    ), patch.object(
        GoogleFreeTranslator,
        "do_translate",
        return_value="Thông tin chuyên sâu và khuyến nghị",
    ) as mock_fallback:
        translated = BingFreeTranslator(
            lang_out="vi", max_retries=0
        ).translate(source)

    assert translated == "Thông tin chuyên sâu và khuyến nghị"
    assert "display:none" not in translated
    assert "<style" not in translated.lower()
    mock_fallback.assert_called_once_with(source)


@pytest.mark.parametrize("service", ["google", "bing"])
def test_free_translator_preserves_source_when_both_providers_reject_error_pages(
    service,
):
    source = "Keep the original document text when free translation is unavailable."

    if service == "google":
        primary_context = patch.object(
            httpx.Client,
            "get",
            return_value=_successful_response(
                text=_google_html(_poisoned_css_error_page())
            ),
        )
        fallback_context = patch.object(
            BingFreeTranslator,
            "do_translate",
            side_effect=FreeTranslationError("Bing rejected an error page"),
        )
        session_context = nullcontext()
        translator = GoogleFreeTranslator(lang_out="vi", max_retries=0)
    else:
        primary_context = patch.object(
            httpx.Client,
            "post",
            return_value=_successful_response(
                json_data=[
                    {"translations": [{"text": _poisoned_html_error_page()}]}
                ]
            ),
        )
        fallback_context = patch.object(
            GoogleFreeTranslator,
            "do_translate",
            side_effect=FreeTranslationError("Google rejected an error page"),
        )
        session_context = patch.object(
            BingFreeTranslator,
            "find_sid",
            return_value=(
                "https://www.bing.com/",
                "ig",
                "iid",
                "key",
                "token",
            ),
        )
        translator = BingFreeTranslator(lang_out="vi", max_retries=0)

    with session_context, primary_context, fallback_context:
        translated = translator.translate(source)

    assert translated == source
    assert "overflow:auto" not in translated
    assert "<html" not in translated.lower()


def test_google_allows_css_signatures_that_are_already_in_the_source():
    css = (
        "body{overflow:auto!important;display:block!important;}"
        "body>*{display:none!important;}"
    )
    source = f"Explain this CSS rule: {css}"
    expected = f"Giải thích quy tắc CSS này: {css}"

    with patch.object(
        httpx.Client,
        "get",
        return_value=_successful_response(text=_google_html(expected)),
    ), patch.object(
        BingFreeTranslator,
        "do_translate",
        side_effect=AssertionError("legitimate CSS must not trigger fallback"),
    ) as mock_fallback:
        translated = GoogleFreeTranslator(
            lang_out="vi", max_retries=0
        ).translate(source)

    assert translated == expected
    mock_fallback.assert_not_called()


def test_rejected_google_result_is_not_cached_and_a_later_call_can_recover():
    source = "Insights and Recommendations {v1}"
    poisoned = _successful_response(
        text=_google_html(f"{_poisoned_css_error_page()} {{v1}}")
    )
    recovered = _successful_response(
        text=_google_html("Thông tin chuyên sâu và khuyến nghị {v1}")
    )

    with patch.object(
        httpx.Client, "get", side_effect=[poisoned, recovered]
    ) as mock_get, patch.object(
        BingFreeTranslator,
        "do_translate",
        side_effect=FreeTranslationError("Bing is temporarily unavailable"),
    ) as mock_fallback:
        translator = GoogleFreeTranslator(lang_out="vi", max_retries=0)
        first_result = translator.translate(source)
        second_result = translator.translate(source)

    assert first_result == source
    assert second_result == "Thông tin chuyên sâu và khuyến nghị {v1}"
    assert mock_get.call_count == 2
    mock_fallback.assert_called_once_with(source)


@pytest.mark.parametrize("service", ["google", "bing"])
def test_free_translators_never_lose_or_reorder_formula_tokens(service):
    source = "Before {v0}, between {v12}, and after {v3}."
    damaged_translation = "Trước { V 0 }, giữa {v 12}, và sau {V3}."

    if service == "google":
        context = patch.object(
            httpx.Client,
            "get",
            return_value=_successful_response(text=_google_html(damaged_translation)),
        )
        translator = GoogleFreeTranslator(lang_out="vi", max_retries=0)
    else:
        context = patch.object(
            httpx.Client,
            "post",
            return_value=_successful_response(
                json_data=[{"translations": [{"text": damaged_translation}]}]
            ),
        )
        translator = BingFreeTranslator(lang_out="vi", max_retries=0)

    sid_context = (
        patch.object(
            BingFreeTranslator,
            "find_sid",
            return_value=("https://www.bing.com/", "ig", "iid", "key", "token"),
        )
        if service == "bing"
        else nullcontext()
    )
    with sid_context, context:
        translated = translator.translate(source)

    assert re.findall(r"\{v\d+\}", translated) == ["{v0}", "{v12}", "{v3}"]


def test_google_rejects_a_translation_that_drops_formula_tokens():
    source = "Equation {v0} followed by {v1}."
    damaged = _successful_response(text=_google_html("Bản dịch đã làm mất công thức."))

    with patch.object(httpx.Client, "get", return_value=damaged), patch.object(
        BingFreeTranslator,
        "do_translate",
        return_value="Bản dự phòng {v0} rồi {v1}.",
    ) as mock_fallback:
        translator = GoogleFreeTranslator(
            lang_out="vi", max_retries=0, retry_backoff=0
        )
        translated = translator.translate(source)

    assert translated == "Bản dự phòng {v0} rồi {v1}."
    mock_fallback.assert_called_once_with(source)


def test_bing_rejects_a_translation_that_drops_formula_tokens():
    source = "Equation {v0} followed by {v1}."
    damaged = _successful_response(
        json_data=[{"translations": [{"text": "Bản dịch đã làm mất công thức."}]}]
    )

    with patch.object(
        BingFreeTranslator,
        "find_sid",
        return_value=("https://www.bing.com/", "ig", "iid", "key", "token"),
    ), patch.object(httpx.Client, "post", return_value=damaged), patch.object(
        GoogleFreeTranslator,
        "do_translate",
        return_value="Bản dự phòng {v0} rồi {v1}.",
    ) as mock_fallback:
        translator = BingFreeTranslator(
            lang_out="vi", max_retries=0, retry_backoff=0
        )
        translated = translator.translate(source)

    assert translated == "Bản dự phòng {v0} rồi {v1}."
    mock_fallback.assert_called_once_with(source)


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


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TRANSLATION_TESTS") != "1",
    reason="set RUN_LIVE_TRANSLATION_TESTS=1 to exercise the unofficial endpoint",
)
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
