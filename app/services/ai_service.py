import httpx
from pydantic import BaseModel, Field
from typing import Optional

class AIConfig(BaseModel):
    provider: str = Field(..., description="openai | deepseek | gemini | claude | custom | google | google_free | bing | bing_free")
    api_key: Optional[str] = ""
    model: Optional[str] = None
    base_url: Optional[str] = None
    custom_prompt: Optional[str] = None
    temperature: float = 0.3

STYLE_DESCRIPTIONS = {
    "Default": "Accurate, natural and context-aware.",
    "Academic": "Formal, academic terminology, rigorous tone.",
    "LaTeX_Beamer": "LaTeX Beamer / Slide presentation format. Reconstruct into full LaTeX Beamer syntax (\\begin{frame}{Title}... \\end{frame}), enclosing blocks (\\begin{block}{Title}... \\end{block}, alertblock, exampleblock), itemize, columns, and beautiful styling.",
    "Business": "Professional, business and executive friendly tone.",
    "Technical": "Precise technical vocabulary, preserving formulas and code terms.",
    "Casual": "Friendly, easy to understand and conversational tone."
}

def build_system_prompt(target_lang: str, style: str, custom_instruction: Optional[str] = None) -> str:
    style_desc = STYLE_DESCRIPTIONS.get(style, STYLE_DESCRIPTIONS["Default"])
    
    is_beamer = (style == "LaTeX_Beamer")
    beamer_rules = ""
    if is_beamer:
        beamer_rules = (
            "\nLATEX BEAMER & SLIDE FORMATTING RULES:\n"
            "1. Output the translated page in proper LaTeX Beamer code for slides:\n"
            "   \\begin{frame}{Translated Frame Title}\n"
            "     \\begin{block}{Section/Block Title}\n"
            "       Content with LaTeX math and styled bullet points...\n"
            "     \\end{block}\n"
            "   \\end{frame}\n"
            "2. Identify blocks, definitions, theorems, alert blocks, and frame titles from the original slide and wrap them in corresponding Beamer environments (\\begin{block}, \\begin{alertblock}, \\begin{exampleblock}, \\begin{definition}, \\begin{theorem}).\n"
            "3. If there are columns or side-by-side components, use \\begin{columns} \\column{0.5\\textwidth} ... \\end{columns}.\n"
        )

    prompt = (
        f"You are a world-class document translator and LaTeX typesetting specialist. "
        f"Translate the given text accurately into {target_lang}.\n"
        f"Translation Style: {style_desc}\n{beamer_rules}\n"
        "CRITICAL RULES FOR MATHEMATICAL EQUATIONS & LATEX:\n"
        "1. PRESERVE ALL LaTeX, mathematical formulas, symbols, and expressions EXACTLY as they appear (e.g., $x_i$, $$\\sum_{i=1}^n x_i$$, \\begin{equation}...\\end{equation}, \\alpha, \\beta, etc.). NEVER translate or damage LaTeX math syntax.\n"
        "2. Wrap inline math expressions in single dollar signs ($...$) and display equations in double dollar signs ($$...$$) or standard LaTeX environments.\n"
        "3. Preserve all citations, reference markers, code snippets, variables, and table structures.\n"
        "4. Output ONLY the translated content. Do NOT add conversational preamble, notes, or explanations."
    )
    if custom_instruction and custom_instruction.strip():
        prompt += f"\n\nAdditional User Instructions: {custom_instruction.strip()}"
    return prompt

async def translate_openai_compatible(text: str, system_prompt: str, config: AIConfig, default_base_url: str, default_model: str) -> str:
    base_url = (config.base_url.rstrip("/") if config.base_url else default_base_url)
    model = config.model if config.model else default_model
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        "temperature": config.temperature,
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"AI Provider error ({resp.status_code}): {resp.text}")
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

async def translate_gemini(text: str, system_prompt: str, config: AIConfig) -> str:
    model = config.model or "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.api_key}"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"temperature": config.temperature}
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini error ({resp.status_code}): {resp.text}")
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()

async def translate_claude(text: str, system_prompt: str, config: AIConfig) -> str:
    model = config.model or "claude-3-5-sonnet-20241022"
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": config.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": text}],
        "temperature": config.temperature
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic error ({resp.status_code}): {resp.text}")
        data = resp.json()
        return data["content"][0]["text"].strip()

async def translate_text(text: str, target_lang: str, style: str, config: AIConfig) -> str:
    if not text or not text.strip():
        return ""
    
    system_prompt = build_system_prompt(target_lang, style, config.custom_prompt)
    provider = config.provider.lower()
    
    if provider == "openai":
        return await translate_openai_compatible(text, system_prompt, config, "https://api.openai.com/v1", "gpt-4o-mini")
    elif provider == "deepseek":
        return await translate_openai_compatible(text, system_prompt, config, "https://api.deepseek.com/v1", "deepseek-chat")
    elif provider == "gemini":
        return await translate_gemini(text, system_prompt, config)
    elif provider == "claude":
        return await translate_claude(text, system_prompt, config)
    elif provider == "custom":
        if not config.base_url:
            raise ValueError("Custom provider requires Base URL")
        return await translate_openai_compatible(text, system_prompt, config, config.base_url, config.model or "default")
    elif provider in ("google", "google_free"):
        import asyncio
        from app.services.pdf2zh_engine.adapter import GoogleFreeTranslator
        translator = GoogleFreeTranslator(lang_in="en", lang_out=target_lang)
        return await asyncio.to_thread(translator.translate, text)
    elif provider in ("bing", "bing_free"):
        import asyncio
        from app.services.pdf2zh_engine.adapter import BingFreeTranslator
        translator = BingFreeTranslator(lang_in="en", lang_out=target_lang)
        return await asyncio.to_thread(translator.translate, text)
    else:
        raise ValueError(f"Unsupported provider: {config.provider}")

async def test_api_connection(config: AIConfig) -> dict:
    test_result = await translate_text("Hello", "Vietnamese", "Default", config)
    return {"status": "success", "sample_translation": test_result}
