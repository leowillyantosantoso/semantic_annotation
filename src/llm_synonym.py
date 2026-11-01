"""
Modul untuk generate synonym variabel CellML via LLM.

The provider is chosen with LLM_PROVIDER in config.py:
    "claude"  - Anthropic API (needs ANTHROPIC_API_KEY)
    "openai"  - OpenAI / ChatGPT API (needs OPENAI_API_KEY)
    "ollama"  - free local model through Ollama (e.g. llama3.3:70b), no API key
"""

import json
import re

from src.prompt import PROMPT_TEMPLATE, INFER_PROMPT_TEMPLATE, PROCESS_PROMPT_TEMPLATE
from config import (
    LLM_PROVIDER,
    CLAUDE_MODEL, CLAUDE_EFFORT, ANTHROPIC_API_KEY,
    OPENAI_MODEL, OPENAI_API_KEY,
    OLLAMA_MODEL, OLLAMA_HOST,
)

# Structured output: the LLM must answer with exactly this JSON shape
SYNONYM_SCHEMA = {
    "type": "object",
    "properties": {
        "symbolic": {"type": "array", "items": {"type": "string"}},
        "textual": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["symbolic", "textual"],
    "additionalProperties": False
}

INFER_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]}
    },
    "required": ["description", "confidence"],
    "additionalProperties": False
}

_KEYWORDS = {"type": "array", "items": {"type": "string"}}

# Stage 3: one participant per moving chemical, each with its own source and sink.
PROCESS_SCHEMA = {
    "type": "object",
    "properties": {
        "variable_role": {"type": "string",
                          "enum": ["transport_current", "parameter", "stimulus_current", "total_current"]},
        "mediator": {"type": "string"},
        "mediator_keywords": _KEYWORDS,
        "participants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chemical": {"type": "string"},
                    "chemical_keywords": _KEYWORDS,
                    "source": {"type": "string"},
                    "source_keywords": _KEYWORDS,
                    "sink": {"type": "string"},
                    "sink_keywords": _KEYWORDS
                },
                "required": ["chemical", "chemical_keywords", "source", "source_keywords", "sink", "sink_keywords"],
                "additionalProperties": False
            }
        },
        "evidence": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "rationale": {"type": "string"}
    },
    "required": ["variable_role", "mediator", "mediator_keywords", "participants",
                 "evidence", "confidence", "rationale"],
    "additionalProperties": False
}

_client = None


def _get_client():
    """Create the client for LLM_PROVIDER once (API keys from config.py)."""
    global _client
    if _client is None:
        if LLM_PROVIDER == "claude":
            import anthropic
            _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
        elif LLM_PROVIDER == "openai":
            import openai
            _client = openai.OpenAI(api_key=OPENAI_API_KEY or None)
        elif LLM_PROVIDER == "ollama":
            import ollama
            _client = ollama.Client(host=OLLAMA_HOST)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'. Use 'claude', 'openai' or 'ollama'.")
    return _client


def generate_synonyms(variable_info, paper_title):
    """Generate synonym untuk satu variabel CellML via the configured LLM.

    Args:
        variable_info: Dict dengan keys "component", "variable", "unit", "formula"
        paper_title: Judul paper PDF

    Returns:
        Dict dengan keys "symbolic" dan "textual"
    """
    prompt = PROMPT_TEMPLATE.format(
        variable=variable_info["variable"],
        component=variable_info["component"],
        unit=variable_info["unit"],
        formula=variable_info.get("formula") or "unknown",
        paper_title=paper_title
    )
    return _clean_synonyms(_ask(prompt, variable_info["variable"], SYNONYM_SCHEMA))


def describe_from_formula(variable_info, paper_title):
    """Ask the LLM what a variable represents, from its name, unit and formula only.

    Used for variables the paper never mentions. The result is an inference, not evidence:
    it is stored apart from the contexts found in the paper.

    Returns:
        Dict dengan keys "description" dan "confidence" ("high" | "medium" | "low")
    """
    prompt = INFER_PROMPT_TEMPLATE.format(
        variable=variable_info["variable"],
        component=variable_info["component"],
        unit=variable_info["unit"],
        formula=variable_info.get("formula") or "unknown",
        paper_title=paper_title
    )
    return _ask(prompt, variable_info["variable"], INFER_SCHEMA)


def annotate_process(variable_info, paper_title, evidence, used_in):
    """Ask the LLM which biological process a variable represents (Stage 3).

    Args:
        variable_info: Stage 2 result for the variable (component, variable, unit, formula, synonyms)
        paper_title: Title of the paper
        evidence: Numbered evidence items, already formatted as text
        used_in: Other CellML equations that use the variable, already formatted as text

    Returns:
        Dict in the PROCESS_SCHEMA shape
    """
    prompt = PROCESS_PROMPT_TEMPLATE.format(
        variable=variable_info["variable"],
        unit=variable_info["unit"],
        component=variable_info.get("formula_component") or variable_info["component"],
        formula=variable_info.get("formula") or "unknown",
        used_in=used_in,
        names=", ".join(variable_info.get("synonyms", {}).get("textual", [])[:5]) or "none",
        paper_title=paper_title,
        evidence=evidence
    )
    return _ask(prompt, variable_info["variable"], PROCESS_SCHEMA)


def _ask(prompt, variable, schema):
    ask = {"claude": _ask_claude, "openai": _ask_openai, "ollama": _ask_ollama}[LLM_PROVIDER]
    return json.loads(_extract_json(ask(prompt, variable, schema)))


def _ask_claude(prompt, variable, schema):
    response = _get_client().beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if Claude declines, the API retries on a fallback model
        output_config={
            "effort": CLAUDE_EFFORT,
            "format": {"type": "json_schema", "schema": schema}
        },
        messages=[{"role": "user", "content": prompt}]
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined to answer for {variable}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"Claude response was cut off for {variable}")

    return next(block.text for block in response.content if block.type == "text")


def _ask_openai(prompt, variable, schema):
    response = _get_client().responses.create(
        model=OPENAI_MODEL,
        input=[{"role": "user", "content": prompt}],
        text={"format": {"type": "json_schema", "name": "answer", "schema": schema, "strict": True}}
    )

    if response.status == "incomplete":
        raise RuntimeError(f"OpenAI response was cut off for {variable}: {response.incomplete_details}")

    return response.output_text


def _ask_ollama(prompt, variable, schema):
    # format=<schema> makes Ollama constrain the output to this JSON shape
    response = _get_client().chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        format=schema,
        options={"temperature": 0}
    )
    return response["message"]["content"]


def _clean_synonyms(synonyms):
    """Trim, drop empty values and remove duplicates (also across symbolic/textual)."""
    seen = set()
    cleaned = {"symbolic": [], "textual": []}
    for key in ("symbolic", "textual"):
        for value in synonyms.get(key, []):
            value = value.strip()
            if value and value.lower() not in seen:
                seen.add(value.lower())
                cleaned[key].append(value)
    return cleaned


def _extract_json(text):
    """Extract JSON from an LLM response text.

    LLMs sometimes wrap JSON in a ```json ... ``` code block.
    This function tries to pull out the JSON regardless of the format.
    """
    text = text.strip()

    # Return as-is if it is already bare JSON
    if text.startswith("{"):
        return text

    # Try to extract from a markdown code block: ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Fallback: take everything between the first { and the last }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        return text[first:last + 1]

    # If all else fails, return the original text (let json.loads raise the error)
    return text
