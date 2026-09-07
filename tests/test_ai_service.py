import pytest
from app.services.ai_service import AIConfig, build_system_prompt, translate_text

def test_build_system_prompt():
    prompt = build_system_prompt("Vietnamese", "Academic")
    assert "Vietnamese" in prompt
    assert "academic" in prompt.lower()
    assert "LaTeX" in prompt
    assert "Detect the source language automatically" in prompt

    explicit_prompt = build_system_prompt(
        "Vietnamese", "Academic", source_lang="Japanese"
    )
    assert "source language is Japanese" in explicit_prompt

@pytest.mark.asyncio
async def test_translate_text_empty():
    config = AIConfig(provider="openai", api_key="dummy", model="gpt-4o-mini")
    res = await translate_text("", "Vietnamese", "Default", config)
    assert res == ""
