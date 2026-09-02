import pytest
from app.services.ai_service import AIConfig, build_system_prompt, translate_text

def test_build_system_prompt():
    prompt = build_system_prompt("Vietnamese", "Academic")
    assert "Vietnamese" in prompt
    assert "academic" in prompt.lower()
    assert "LaTeX" in prompt

@pytest.mark.asyncio
async def test_translate_text_empty():
    config = AIConfig(provider="openai", api_key="dummy", model="gpt-4o-mini")
    res = await translate_text("", "Vietnamese", "Default", config)
    assert res == ""
