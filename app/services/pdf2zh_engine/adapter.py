import html
import logging
import re
import unicodedata
from typing import Any, Optional
import httpx

from app.services.ai_service import AIConfig

logger = logging.getLogger(__name__)


def remove_control_characters(s: str) -> str:
    """Remove non-printable control characters except standard whitespace."""
    return "".join(ch for ch in s if unicodedata.category(ch)[0] != "C" or ch in ("\n", "\r", "\t"))


def normalize_tokens(text: str) -> str:
    """
    Normalize formula marker tokens like { v0 }, {v 1}, {V2} to {v0}, {v1}, {v2}.
    """
    if not text:
        return ""
    return re.sub(r"\{\s*[vV]\s*(\d+)\s*\}", r"{v\1}", text)


class BaseTranslator:
    """Base class for all PDF2ZH translators with caching and formula token bypass."""

    name: str = "base"
    lang_in: str = "en"
    lang_out: str = "vi"

    def __init__(
        self,
        name: str = "base",
        lang_in: str = "en",
        lang_out: str = "vi",
        **kwargs: Any,
    ):
        self.name = name
        self.lang_in = lang_in
        self.lang_out = lang_out
        self._cache: dict[str, str] = {}

    def translate(self, text: str) -> str:
        """
        Translate the given text.
        Bypasses translation if text is empty/whitespace or is a single formula token like {v0}.
        Caches results to avoid duplicate translation calls.
        """
        if not text or not text.strip():
            return text

        # If text is solely a formula token (e.g. "{v0}", "{v12}"), return directly
        if re.match(r"^\{v\d+\}$", text.strip()):
            return text

        if text in self._cache:
            return self._cache[text]

        translated = self.do_translate(text)
        translated = normalize_tokens(translated)
        self._cache[text] = translated
        return translated

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Translate a batch of text segments."""
        return [self.translate(t) for t in texts]

    def do_translate(self, text: str) -> str:
        """Subclasses must implement actual translation logic here."""
        raise NotImplementedError

    def clear_cache(self) -> None:
        """Clear the translation cache."""
        self._cache.clear()


class GoogleFreeTranslator(BaseTranslator):
    """Free Google Translate web API without requiring an API key."""

    name: str = "google"
    lang_map: dict[str, str] = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "zh-tw": "zh-TW",
        "zh-hans": "zh-CN",
        "zh-hant": "zh-TW",
    }

    def __init__(
        self,
        lang_in: str = "en",
        lang_out: str = "vi",
        timeout: float = 15.0,
        **kwargs: Any,
    ):
        in_code = self.lang_map.get(lang_in.lower(), lang_in)
        out_code = self.lang_map.get(lang_out.lower(), lang_out)
        super().__init__(name="google", lang_in=in_code, lang_out=out_code, **kwargs)
        self.endpoint = "https://translate.google.com/m"
        self.headers = {
            "User-Agent": "Mozilla/4.0 (compatible;MSIE 6.0;Windows NT 5.1;SV1;.NET CLR 1.1.4322;.NET CLR 2.0.50727;.NET CLR 3.0.04506.30)"
        }
        self.timeout = timeout

    def do_translate(self, text: str) -> str:
        text_truncated = text[:5000]
        with httpx.Client(headers=self.headers, timeout=self.timeout) as client:
            response = client.get(
                self.endpoint,
                params={"tl": self.lang_out, "sl": self.lang_in, "q": text_truncated},
            )
            if response.status_code == 400:
                logger.warning(f"Google translate 400 error for text: {text[:50]}...")
                return text
            response.raise_for_status()

        # Extract translation from response
        matches = re.findall(r'(?s)class="(?:t0|result-container)">(.*?)<', response.text)
        if matches:
            result = html.unescape(matches[0])
        else:
            m = re.search(r'class="[^"]*(?:t0|result-container)[^"]*"[^>]*>(.*?)</div>', response.text, re.DOTALL)
            if m:
                result = html.unescape(m.group(1))
            else:
                result = text

        return remove_control_characters(result)


class BingFreeTranslator(BaseTranslator):
    """Free Bing Translate web API with automatic fallback to Google."""

    name: str = "bing"
    lang_map: dict[str, str] = {
        "zh": "zh-Hans",
        "zh-cn": "zh-Hans",
        "zh-tw": "zh-Hant",
        "zh-hans": "zh-Hans",
        "zh-hant": "zh-Hant",
    }

    def __init__(
        self,
        lang_in: str = "en",
        lang_out: str = "vi",
        timeout: float = 15.0,
        **kwargs: Any,
    ):
        in_code = self.lang_map.get(lang_in.lower(), lang_in)
        out_code = self.lang_map.get(lang_out.lower(), lang_out)
        super().__init__(name="bing", lang_in=in_code, lang_out=out_code, **kwargs)
        self.endpoint = "https://www.bing.com/translator"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        }
        self.timeout = timeout

    def find_sid(self) -> tuple[str, str, str, str, str]:
        with httpx.Client(headers=self.headers, timeout=self.timeout) as client:
            response = client.get(self.endpoint)
            response.raise_for_status()
            url_str = str(response.url)
            url_base = url_str.rsplit("/translator", 1)[0] + "/"
            ig = re.findall(r'\"ig\":\"(.*?)\"', response.text)[0]
            iid = re.findall(r'data-iid=\"(.*?)\"', response.text)[-1]
            key, token = re.findall(
                r'params_AbusePreventionHelper\s*=\s*\[(.*?),\"(.*?)\"', response.text
            )[0]
            return url_base, ig, iid, key, token

    def do_translate(self, text: str) -> str:
        text_truncated = text[:1000]
        try:
            url_base, ig, iid, key, token = self.find_sid()
            t_url = f"{url_base}ttranslatev3?IG={ig}&IID={iid}"
            with httpx.Client(headers=self.headers, timeout=self.timeout) as client:
                response = client.post(
                    t_url,
                    data={
                        "fromLang": self.lang_in,
                        "to": self.lang_out,
                        "text": text_truncated,
                        "token": token,
                        "key": key,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data[0]["translations"][0]["text"]
        except Exception as e:
            logger.warning(f"Bing translator failed ({e}), falling back to GoogleFreeTranslator")
            fallback = GoogleFreeTranslator(lang_in=self.lang_in, lang_out=self.lang_out, timeout=self.timeout)
            return fallback.do_translate(text)


def build_llm_system_prompt(target_lang: str, custom_prompt: Optional[str] = None) -> str:
    """Build the system prompt with strict formula marker preservation rules."""
    prompt = (
        f"You are an expert document translator. Translate the given text into {target_lang}.\n"
        "CRITICAL RULES:\n"
        "1. The text contains formula markers like {v0}, {v1}, {v2}...\n"
        "2. You MUST preserve all {v\\d+} markers EXACTLY as they appear in the translation.\n"
        "3. Do NOT translate, alter, remove, or change the order of {v\\d+} markers.\n"
        "4. Return ONLY the translated text, no conversational text or explanations."
    )
    if custom_prompt and custom_prompt.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_prompt.strip()}"
    return prompt


class LLMTranslator(BaseTranslator):
    """Synchronous LLM translator using httpx for multi-provider support."""

    name: str = "llm"

    def __init__(
        self,
        config: AIConfig,
        lang_in: str = "en",
        lang_out: str = "vi",
        timeout: float = 90.0,
        **kwargs: Any,
    ):
        super().__init__(name="llm", lang_in=lang_in, lang_out=lang_out, **kwargs)
        self.config = config
        self.timeout = timeout
        self.system_prompt = build_llm_system_prompt(self.lang_out, config.custom_prompt)

    def _translate_openai_compatible(
        self, text: str, default_base_url: str, default_model: str
    ) -> str:
        base_url = (self.config.base_url.rstrip("/") if self.config.base_url else default_base_url)
        model = self.config.model if self.config.model else default_model
        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": self.config.temperature,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"AI Provider error ({resp.status_code}): {resp.text}")
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    def _translate_gemini(self, text: str) -> str:
        model = self.config.model or "gemini-1.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.config.api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": self.system_prompt}]},
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"temperature": self.config.temperature},
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini error ({resp.status_code}): {resp.text}")
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()

    def _translate_claude(self, text: str) -> str:
        model = self.config.model or "claude-3-5-sonnet-20241022"
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": self.system_prompt,
            "messages": [{"role": "user", "content": text}],
            "temperature": self.config.temperature,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Anthropic error ({resp.status_code}): {resp.text}")
            data = resp.json()
            return data["content"][0]["text"].strip()

    def do_translate(self, text: str) -> str:
        provider = self.config.provider.lower()
        if provider == "openai":
            return self._translate_openai_compatible(text, "https://api.openai.com/v1", "gpt-4o-mini")
        elif provider == "deepseek":
            return self._translate_openai_compatible(text, "https://api.deepseek.com/v1", "deepseek-chat")
        elif provider == "gemini":
            return self._translate_gemini(text)
        elif provider == "claude":
            return self._translate_claude(text)
        elif provider == "custom":
            if not self.config.base_url:
                raise ValueError("Custom provider requires Base URL")
            return self._translate_openai_compatible(
                text, self.config.base_url, self.config.model or "default"
            )
        else:
            raise ValueError(f"Unsupported provider: {self.config.provider}")


class PDF2ZHAdapter(BaseTranslator):
    """Adapter wrapping an arbitrary translation engine or function with token preservation."""

    def __init__(
        self,
        engine: Any = None,
        lang_in: str = "en",
        lang_out: str = "vi",
        **kwargs: Any,
    ):
        super().__init__(name="adapter", lang_in=lang_in, lang_out=lang_out, **kwargs)
        self.engine = engine

    def do_translate(self, text: str) -> str:
        if self.engine is not None:
            if hasattr(self.engine, "translate"):
                return self.engine.translate(text)
            elif callable(self.engine):
                return self.engine(text)
            else:
                raise ValueError("Engine must have a translate method or be callable")
        return text


def create_translator(
    provider: str,
    target_lang: str,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    custom_prompt: str = "",
    temperature: float = 0.3,
    source_lang: str = "en",
) -> BaseTranslator:
    """
    Factory creating appropriate translator:
    - google / google_free -> GoogleFreeTranslator
    - bing / bing_free -> BingFreeTranslator
    - openai / deepseek / gemini / claude / custom -> LLMTranslator
    """
    provider_norm = provider.lower() if provider else "google_free"
    if provider_norm in ("google", "google_free"):
        return GoogleFreeTranslator(lang_in=source_lang, lang_out=target_lang)
    elif provider_norm in ("bing", "bing_free"):
        return BingFreeTranslator(lang_in=source_lang, lang_out=target_lang)
    else:
        config = AIConfig(
            provider=provider_norm,
            api_key=api_key,
            model=model or None,
            base_url=base_url or None,
            custom_prompt=custom_prompt or None,
            temperature=temperature,
        )
        return LLMTranslator(config=config, lang_in=source_lang, lang_out=target_lang)


def create_adapter(
    provider: str,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    lang_in: str = "en",
    lang_out: str = "vi",
    custom_prompt: str = "",
    temperature: float = 0.3,
    target_lang: str = "",
) -> BaseTranslator:
    """Convenience factory compatible with create_adapter parameter schema."""
    out_lang = target_lang or lang_out
    return create_translator(
        provider=provider,
        target_lang=out_lang,
        api_key=api_key,
        model=model,
        base_url=base_url,
        custom_prompt=custom_prompt,
        temperature=temperature,
        source_lang=lang_in,
    )
