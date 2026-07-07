PROMPT_TEMPLATE = """
You are an expert in cardiac electrophysiology and biomedical modeling.

Your task is to generate high-quality synonyms and alternative expressions for a CellML variable to support scientific literature retrieval.

Given:

Variable name: {variable}

Component: {component}

Unit: {unit}

Paper title:
{paper_title}

Instructions:


1. Generate synonyms that are commonly used in peer-reviewed scientific literature.

2. Include:
- Symbol variants
- Common abbreviations
- Expanded textual names

3. Preserve scientific accuracy.

4. Do NOT invent new biological meanings.

5. Do NOT generate unrelated concepts.

6. If a synonym is uncertain, omit it instead of guessing.

7. Every synonym must be:
- trimmed (no leading/trailing spaces)
- unique
- scientifically valid

8. Prefer exact symbolic forms that are commonly used in CellML models or peer-reviewed literature.

9. Avoid generating ambiguous or overly generic symbolic forms that could match unrelated terms.

10. If multiple symbolic variants exist, prefer the most widely accepted scientific notation.

11. Do not repeat duplicate values across the symbolic and textual lists.

12. Order the synonyms from the most commonly used to the least commonly used.

13. Return ONLY valid JSON.

14. Do NOT wrap the JSON inside markdown (for example, do not use ```json).

15. Generate synonyms and alternative expressions ONLY in English. Do not output translations or characters in other languages (such as Chinese, Japanese, etc.).

Output format:

{{
    "symbolic": [
        ...
    ],
    "textual": [
        ...
    ]
}}


"""