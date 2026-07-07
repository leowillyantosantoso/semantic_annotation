"""
Prompt template untuk Proses 3: Inferensi proses biologi (format baru).
"""

PROCESS3_PROMPT_TEMPLATE = """You are a **cardiac electrophysiology ontology engineer**. Your task is to infer biological processes from research paper excerpts and convert them into structured annotations following a specific JSON schema.

## Context
- **Variable**: {variable_name}
- **Unit**: {unit}  
- **Component**: {component}
- **Process Type**: Ion channel current

## Supporting Sentences from Paper
{evidence_sentences}

## Your Task

Analyze the supporting sentences and infer the biological process structure. You MUST identify:

1. **ION**: The ion carrying the current (e.g., sodium, potassium, calcium)
2. **SOURCE**: Where the ions come from (intracellular or extracellular)
3. **SINK**: Where the ions go (intracellular or extracellular)
4. **MEDIATOR**: The biological entity enabling the process (e.g., voltage-gated sodium channel, potassium channel)

## Inference Rules (Follow Strictly)

| Ion Type | Current Direction | Source | Sink |
|----------|-------------------|--------|------|
| Sodium (Na+) | Inward | extracellular | intracellular |
| Potassium (K+) | Outward | intracellular | extracellular |
| Calcium (Ca2+) | Inward | extracellular | intracellular |
| Chloride (Cl-) | Varies | Check paper context | Check paper context |

## Output Format (JSON Only)

{{
  "name": "{component_title}",
  "component": "{component}",
  "current_variable": "{variable_name}",
  "mediator": "descriptive_mediator_name",
  "mediator_ontology_keywords": ["mediator_keyword_1", "mediator_keyword_2"],
  "participants": [
    {{
      "ion": "ion_name",
      "ion_ontology_keywords": ["ion_keyword_1", "ion_keyword_2"],
      "source": "source_compartment",
      "source_ontology_keywords": ["source_keyword_1"],
      "sink": "sink_compartment",
      "sink_ontology_keywords": ["sink_keyword_1"]
    }}
  ]
}}

## Critical Rules

1. **NEVER invent ontology IDs** - ONLY provide search keywords (e.g., "voltage-gated sodium channel", "sodium", "intracellular")

2. **Use exact keywords from the paper** when possible

3. **Do NOT output generic placeholders** like "GO_search_term1", "CHEBI_search_term1", "FMA_search_term1", "mediator_keyword_1", or similar in the list of keywords. You MUST replace them with real, concrete keywords based on the variable and evidence sentences (e.g. "potassium", "extracellular", "sodium-calcium exchanger").

4. **For mediators**, use descriptive names:
   - Sodium channel: "voltage-gated sodium channel"
   - Potassium channel: "voltage-gated potassium channel"
   - Calcium channel: "voltage-gated calcium channel"
   - Na/K pump: "sodium-potassium ATPase"
   - NCX: "sodium-calcium exchanger"

5. **For ions**, use standard names:
   - "sodium", "potassium", "calcium", "chloride"

6. **For compartments**, use standard terms:
   - "intracellular"
   - "extracellular"

7. **Return ONLY valid JSON** - no explanatory text, no markdown formatting

Now analyze the supporting sentences and produce the annotation:"""
