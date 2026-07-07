"""
Modul untuk generate synonym variabel CellML via LLM (Ollama).
"""

import json
import re
import ollama

from src.prompt import PROMPT_TEMPLATE
from config import MODEL_NAME


def generate_synonyms(variable_info, paper_title):
    """Generate synonym untuk satu variabel CellML via LLM.

    Args:
        variable_info: Dict dengan keys "component", "variable", "unit"
        paper_title: Judul paper PDF

    Returns:
        Dict dengan keys "symbolic" dan "textual"
    """
    from src.database import check_llm_cache, save_llm_cache

    prompt = PROMPT_TEMPLATE.format(
        variable=variable_info["variable"],
        component=variable_info["component"],
        unit=variable_info["unit"],
        paper_title=paper_title
    )

    cached_content = check_llm_cache(prompt)
    if cached_content:
        content = cached_content
        print("    [LLM Cache Hit] Menggunakan response sinonim dari cache.")
    else:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
        content = response["message"]["content"]
        save_llm_cache(prompt, content)

    print("\n===== RAW LLM RESPONSE =====")
    print(content)
    print("============================\n")

    # Ekstrak JSON dari response (handle markdown code block)
    json_text = _extract_json(content)
    return json.loads(json_text)


def _extract_json(text):
    """Ekstrak JSON dari text LLM response.

    LLM kadang bungkus JSON dalam ```json ... ``` code block.
    Fungsi ini coba ambil JSON-nya, apapun formatnya.
    """
    text = text.strip()

    # Coba langsung parse (kalau sudah bare JSON)
    if text.startswith("{"):
        return text

    # Coba ambil dari markdown code block: ```json ... ``` atau ``` ... ```
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Fallback: ambil antara { pertama dan } terakhir
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        return text[first:last + 1]

    # Kalau tetap gagal, kembalikan text asli (biar json.loads yang raise error)
    return text
