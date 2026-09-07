import html
import logging
import re
import ssl
import threading
import time
import unicodedata
from html.parser import HTMLParser
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import urlparse

import httpx

from app.services.ai_service import AIConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")
_SYSTEM_SSL_CONTEXT = ssl.create_default_context()

_FORMULA_TOKEN_RE = re.compile(r"\{\s*[vV]\s*\d+\s*\}")
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_ERROR_PAGE_COMPACT_MARKERS = (
    "<!doctypehtml",
    "<html",
    "<head",
    "<body",
    "<style",
    "<script",
    "#af-error-page",
    "body>*{display:none!important",
    "body{overflow:auto!important",
    "cf-chl-",
    "g-recaptcha",
)
_ERROR_PAGE_PHRASES = (
    "our systems have detected unusual traffic",
    "unusual traffic from your computer network",
    "to continue, please type the characters below",
    "please verify that you are not a robot",
)

_LANGUAGE_ALIASES = {
    "vietnamese": "vi",
    "tiếng việt": "vi",
    "english": "en",
    "japanese": "ja",
    "chinese": "zh",
    "simplified chinese": "zh-hans",
    "traditional chinese": "zh-hant",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "russian": "ru",
    "portuguese": "pt",
    "italian": "it",
    "dutch": "nl",
    "polish": "pl",
    "ukrainian": "uk",
    "thai": "th",
    "indonesian": "id",
    "malay": "ms",
    "arabic": "ar",
    "hindi": "hi",
    "turkish": "tr",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "czech": "cs",
    "romanian": "ro",
    "hungarian": "hu",
    "greek": "el",
    "hebrew": "he",
}

_AUTO_LANGUAGE_ALIASES = {
    "",
    "auto",
    "auto-detect",
    "detect",
    "detect language",
    "automatic",
}


class FreeTranslationError(RuntimeError):
    """Raised when an unofficial free web translator cannot return a safe result."""


class _PreserveSourceText(FreeTranslationError):
    """Signal a graceful, deliberately non-cacheable source-text fallback."""


def validate_free_translation(source: str, translated: str, provider: str) -> None:
    """Reject browser, challenge and error documents masquerading as translations."""
    source_unescaped = html.unescape(source).casefold()
    translated_unescaped = html.unescape(translated).casefold()
    source_compact = re.sub(r"\s+", "", source_unescaped)
    translated_compact = re.sub(r"\s+", "", translated_unescaped)

    for marker in _ERROR_PAGE_COMPACT_MARKERS:
        if marker in translated_compact and marker not in source_compact:
            raise FreeTranslationError(
                f"{provider} returned a browser error page instead of a translation"
            )

    if (
        translated_unescaped.count("!important") >= 2
        and source_unescaped.count("!important") < 2
    ):
        raise FreeTranslationError(
            f"{provider} returned error-page CSS instead of a translation"
        )

    for phrase in _ERROR_PAGE_PHRASES:
        if phrase in translated_unescaped and phrase not in source_unescaped:
            raise FreeTranslationError(
                f"{provider} returned a web challenge instead of a translation"
            )


def normalize_language_code(language: str, provider: str, *, source: bool) -> str:
    """Normalize UI language names to the codes expected by Google/Bing web UIs."""
    raw = (language or "").strip()
    key = raw.lower().replace("_", "-")

    if key in _AUTO_LANGUAGE_ALIASES:
        if not source:
            raise ValueError("Target language cannot use automatic detection")
        return "auto-detect" if provider == "bing" else "auto"

    code = _LANGUAGE_ALIASES.get(key, key)
    if key not in _LANGUAGE_ALIASES and not re.fullmatch(
        r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", key
    ):
        raise ValueError(f"Unsupported language for free translation: {raw}")
    if provider == "bing":
        if code in {"zh", "zh-cn", "zh-hans"}:
            return "zh-Hans"
        if code in {"zh-tw", "zh-hant"}:
            return "zh-Hant"
    else:
        if code in {"zh", "zh-cn", "zh-hans"}:
            return "zh-CN"
        if code in {"zh-tw", "zh-hant"}:
            return "zh-TW"

    return code


def split_translation_chunks(text: str, max_chars: int) -> list[str]:
    """Split text without dropping characters or cutting through formula markers."""
    if max_chars < 32:
        raise ValueError("max_chars must be at least 32")
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    text_len = len(text)
    preferred_boundaries = re.compile(
        r"\n{2,}|\n|(?<=[.!?。！？])\s+|(?<=[;:；：])\s+|,\s+|\s+"
    )

    while start < text_len:
        proposed = min(start + max_chars, text_len)
        if proposed == text_len:
            chunks.append(text[start:])
            break

        window = text[start:proposed]
        matches = list(preferred_boundaries.finditer(window))
        cut = proposed
        minimum_preferred = max(1, max_chars // 3)
        for match in reversed(matches):
            candidate = start + match.end()
            if candidate - start >= minimum_preferred:
                cut = candidate
                break

        # A hard cut must never split a PDF2ZH formula placeholder such as {v12}.
        token_window_start = max(start, cut - 32)
        token_window_end = min(text_len, cut + 32)
        for token_match in _FORMULA_TOKEN_RE.finditer(
            text[token_window_start:token_window_end]
        ):
            token_start = token_window_start + token_match.start()
            token_end = token_window_start + token_match.end()
            if token_start < cut < token_end:
                cut = token_start if token_start > start else token_end
                break

        if cut <= start:
            cut = min(start + max_chars, text_len)
        chunks.append(text[start:cut])
        start = cut

    return chunks


class _GoogleResultParser(HTMLParser):
    """Extract the translated result while tolerating nested markup changes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture_depth = 0
        self._finished = False
        self.unsafe_markup = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self._finished:
            return
        if self._capture_depth:
            if tag.lower() in {
                "body",
                "head",
                "html",
                "iframe",
                "script",
                "style",
            }:
                self.unsafe_markup = True
            if tag.lower() == "br":
                self.parts.append("\n")
                return
            if tag.lower() in {
                "area",
                "base",
                "embed",
                "hr",
                "img",
                "input",
                "link",
                "meta",
                "source",
                "wbr",
            }:
                return
            self._capture_depth += 1
            return
        classes = dict(attrs).get("class") or ""
        class_names = set(classes.split())
        if {"result-container", "t0"} & class_names:
            self._capture_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if not self._capture_depth:
            return
        self._capture_depth -= 1
        if self._capture_depth == 0:
            self._finished = True

    def handle_data(self, data: str) -> None:
        if self._capture_depth:
            self.parts.append(data)

    @property
    def result(self) -> str:
        return "".join(self.parts).strip()


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


class _FreeWebTranslator(BaseTranslator):
    """Shared resilience, chunking and placeholder validation for free web UIs."""

    handles_retries = True

    def __init__(
        self,
        *,
        name: str,
        lang_in: str,
        lang_out: str,
        timeout: float,
        max_chars: int,
        max_retries: int,
        retry_backoff: float,
        enable_fallback: bool,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, lang_in=lang_in, lang_out=lang_out, **kwargs)
        self.timeout = timeout
        self.max_chars = max_chars
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.enable_fallback = enable_fallback
        self.headers: dict[str, str] = {}

    def translate(self, text: str) -> str:
        try:
            return super().translate(text)
        except _PreserveSourceText:
            # Do not cache this result: a later paragraph or retry may recover
            # after the upstream rate limit / challenge page has cleared.
            return normalize_tokens(text)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in _RETRYABLE_STATUS_CODES
        return isinstance(exc, (httpx.TransportError, FreeTranslationError))

    @staticmethod
    def _error_label(exc: Exception) -> str:
        """Describe failures without logging request URLs or document text."""
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        return type(exc).__name__

    def _can_bypass(self, text: str) -> bool:
        if (
            self.lang_in not in {"auto", "auto-detect"}
            and self.lang_in == self.lang_out
        ):
            return True
        without_tokens = _FORMULA_TOKEN_RE.sub("", text)
        return not any(char.isalpha() for char in without_tokens)

    def _create_client(self) -> httpx.Client:
        """Use the OS trust store while keeping full TLS certificate verification."""
        return httpx.Client(
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
            verify=_SYSTEM_SSL_CONTEXT,
        )

    def _with_retry(self, operation: Callable[[], T]) -> T:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries or not self._is_retryable(exc):
                    raise
                delay = self.retry_backoff * (2**attempt)
                if isinstance(exc, httpx.HTTPStatusError):
                    retry_after = exc.response.headers.get("Retry-After", "")
                    try:
                        delay = max(delay, min(float(retry_after), 5.0))
                    except (TypeError, ValueError):
                        pass
                logger.warning(
                    "%s free translator attempt %s failed; retrying in %.2fs: %s",
                    self.name,
                    attempt + 1,
                    delay,
                    self._error_label(exc),
                )
                if delay:
                    time.sleep(delay)
        raise FreeTranslationError(f"{self.name} translation failed") from last_error

    def _translate_all(
        self, text: str, translate_chunk: Callable[[str], str]
    ) -> str:
        translated_chunks: list[str] = []
        for chunk in split_translation_chunks(text, self.max_chars):
            leading_len = len(chunk) - len(chunk.lstrip())
            trailing_len = len(chunk) - len(chunk.rstrip())
            core_end = len(chunk) - trailing_len if trailing_len else len(chunk)
            leading = chunk[:leading_len]
            core = chunk[leading_len:core_end]
            trailing = chunk[core_end:]

            if not core:
                translated_chunks.append(chunk)
                continue

            translated = normalize_tokens(translate_chunk(core)).strip()
            if not translated:
                raise FreeTranslationError(
                    f"{self.name} returned an empty translation"
                )
            validate_free_translation(core, translated, self.name)

            expected_tokens = [
                normalize_tokens(token) for token in _FORMULA_TOKEN_RE.findall(core)
            ]
            actual_tokens = _FORMULA_TOKEN_RE.findall(translated)
            if expected_tokens != actual_tokens:
                raise FreeTranslationError(
                    f"{self.name} changed PDF formula placeholders"
                )
            translated_chunks.append(f"{leading}{translated}{trailing}")

        return remove_control_characters("".join(translated_chunks))


class GoogleFreeTranslator(_FreeWebTranslator):
    """Unofficial Google Translate web client with chunking and Bing fallback."""

    name: str = "google"

    def __init__(
        self,
        lang_in: str = "auto",
        lang_out: str = "vi",
        timeout: float = 15.0,
        max_chars: int = 1800,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
        enable_fallback: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="google",
            lang_in=normalize_language_code(lang_in, "google", source=True),
            lang_out=normalize_language_code(lang_out, "google", source=False),
            timeout=timeout,
            max_chars=max_chars,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            enable_fallback=enable_fallback,
            **kwargs,
        )
        self.endpoint = "https://translate.google.com/m"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

    @staticmethod
    def _parse_result(response_text: str) -> str:
        parser = _GoogleResultParser()
        parser.feed(response_text)
        if parser.unsafe_markup:
            raise FreeTranslationError(
                "Google result container included unsafe page markup"
            )
        if parser.result:
            return parser.result

        # Compatibility fallback for small historical variations of the mobile page.
        match = re.search(
            r'class=["\'][^"\']*(?:t0|result-container)[^"\']*["\'][^>]*>'
            r'(.*?)</(?:div|span)>',
            response_text,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
        raise FreeTranslationError("Google response did not contain a translation")

    def _translate_chunk(self, client: httpx.Client, text: str) -> str:
        response = client.get(
            self.endpoint,
            params={"tl": self.lang_out, "sl": self.lang_in, "q": text},
        )
        response.raise_for_status()
        return self._parse_result(response.text)

    def do_translate(self, text: str) -> str:
        if self._can_bypass(text):
            return normalize_tokens(text)
        try:
            with self._create_client() as client:
                return self._translate_all(
                    text,
                    lambda chunk: self._with_retry(
                        lambda: self._translate_chunk(client, chunk)
                    ),
                )
        except Exception as exc:
            if not self.enable_fallback:
                raise FreeTranslationError(
                    "Google free translation failed"
                ) from None
            logger.warning(
                "Google free translator failed; falling back to Bing: %s",
                self._error_label(exc),
            )
            fallback = BingFreeTranslator(
                lang_in=self.lang_in,
                lang_out=self.lang_out,
                timeout=self.timeout,
                max_retries=self.max_retries,
                retry_backoff=self.retry_backoff,
                enable_fallback=False,
            )
            try:
                return fallback.do_translate(text)
            except Exception as fallback_exc:
                logger.warning(
                    "Google and Bing free translators both failed; preserving "
                    "the source text: %s",
                    self._error_label(fallback_exc),
                )
                raise _PreserveSourceText from None


class BingFreeTranslator(_FreeWebTranslator):
    """Unofficial Bing Translate web client with session reuse and Google fallback."""

    name: str = "bing"

    def __init__(
        self,
        lang_in: str = "auto",
        lang_out: str = "vi",
        timeout: float = 15.0,
        max_chars: int = 950,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
        enable_fallback: bool = True,
        session_ttl: float = 600.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="bing",
            lang_in=normalize_language_code(lang_in, "bing", source=True),
            lang_out=normalize_language_code(lang_out, "bing", source=False),
            timeout=timeout,
            max_chars=max_chars,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            enable_fallback=enable_fallback,
            **kwargs,
        )
        self.endpoint = "https://www.bing.com/translator"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.session_ttl = max(0.0, session_ttl)
        self._session_credentials: Optional[tuple[str, str, str, str, str]] = None
        self._session_expires_at = 0.0
        self._session_lock = threading.Lock()

    def find_sid(
        self, client: Optional[httpx.Client] = None
    ) -> tuple[str, str, str, str, str]:
        owns_client = client is None
        active_client = client or self._create_client()
        try:
            response = active_client.get(self.endpoint)
            response.raise_for_status()
            response_text = response.text
            url_str = str(response.url)
            url_base = url_str.rsplit("/translator", 1)[0].rstrip("/") + "/"
            parsed_base = urlparse(url_base)
            hostname = (parsed_base.hostname or "").lower()
            if hostname != "bing.com" and not hostname.endswith(".bing.com"):
                raise FreeTranslationError("Bing returned an unexpected translation host")

            ig_match = re.search(
                r'["\'](?:ig|IG)["\']\s*:\s*["\']([^"\']+)',
                response_text,
            )
            iid_matches = re.findall(
                r'data-iid=["\']([^"\']+)', response_text, re.IGNORECASE
            )
            helper_match = re.search(
                r'params_AbusePreventionHelper\s*=\s*\[\s*([^,]+),\s*["\']([^"\']+)',
                response_text,
            )
            if not ig_match or not iid_matches or not helper_match:
                raise FreeTranslationError(
                    "Bing translator session metadata was not found"
                )

            key = helper_match.group(1).strip().strip('"\'')
            token = helper_match.group(2)
            return url_base, ig_match.group(1), iid_matches[-1], key, token
        finally:
            if owns_client:
                active_client.close()

    def _get_session_credentials(
        self, client: httpx.Client, *, force_refresh: bool = False
    ) -> tuple[str, str, str, str, str]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._session_credentials is not None
            and now < self._session_expires_at
        ):
            return self._session_credentials

        with self._session_lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._session_credentials is not None
                and now < self._session_expires_at
            ):
                return self._session_credentials
            credentials = self._with_retry(lambda: self.find_sid(client=client))
            self._session_credentials = credentials
            self._session_expires_at = time.monotonic() + self.session_ttl
            return credentials

    def _translate_chunk(
        self,
        client: httpx.Client,
        text: str,
        credentials: tuple[str, str, str, str, str],
    ) -> str:
        url_base, ig, iid, key, token = credentials
        response = client.post(
            f"{url_base}ttranslatev3?IG={ig}&IID={iid}",
            data={
                "fromLang": self.lang_in,
                "to": self.lang_out,
                "text": text,
                "token": token,
                "key": key,
            },
        )
        response.raise_for_status()
        try:
            data = response.json()
            result = data[0]["translations"][0]["text"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise FreeTranslationError(
                "Bing response did not contain a translation"
            ) from exc
        if not isinstance(result, str):
            raise FreeTranslationError("Bing returned a non-text translation")
        return result

    def do_translate(self, text: str) -> str:
        if self._can_bypass(text):
            return normalize_tokens(text)
        try:
            with self._create_client() as client:
                credentials: Optional[tuple[str, str, str, str, str]] = None

                def translate_chunk(chunk: str) -> str:
                    nonlocal credentials
                    if credentials is None:
                        credentials = self._get_session_credentials(client)
                    try:
                        return self._with_retry(
                            lambda: self._translate_chunk(client, chunk, credentials)
                        )
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code not in {401, 403}:
                            raise
                        credentials = self._get_session_credentials(
                            client, force_refresh=True
                        )
                        return self._with_retry(
                            lambda: self._translate_chunk(client, chunk, credentials)
                        )

                return self._translate_all(text, translate_chunk)
        except Exception as exc:
            if not self.enable_fallback:
                raise FreeTranslationError(
                    "Bing free translation failed"
                ) from None
            logger.warning(
                "Bing free translator failed; falling back to Google: %s",
                self._error_label(exc),
            )
            fallback = GoogleFreeTranslator(
                lang_in=self.lang_in,
                lang_out=self.lang_out,
                timeout=self.timeout,
                max_retries=self.max_retries,
                retry_backoff=self.retry_backoff,
                enable_fallback=False,
            )
            try:
                return fallback.do_translate(text)
            except Exception as fallback_exc:
                logger.warning(
                    "Bing and Google free translators both failed; preserving "
                    "the source text: %s",
                    self._error_label(fallback_exc),
                )
                raise _PreserveSourceText from None


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
    source_lang: str = "auto",
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
    lang_in: str = "auto",
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
