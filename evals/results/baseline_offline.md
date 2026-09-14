# Eval report: baseline_offline

- commit: `2b45d5e`  
- generated: 2026-09-13T23:51:14.970226+00:00  
- scenarios: 72  
- mode: both

## Offline (machine-scored, router only)

| check | routed | legacy |
|---|---|---|
| expected_tools_exposed | 72/72 (100%) | 8/72 (11%) |
| forbidden_tools_absent | 72/72 (100%) | 72/72 (100%) |
| history_isolation | 31/31 (100%) | 3/31 (10%) |
| research_necessity | 72/72 (100%) | 39/72 (54%) |
| route_correct | 72/72 (100%) | 16/72 (22%) |
| safety_gate | 72/72 (100%) | 64/72 (89%) |

Paraphrase consistency (routed): {'tonight': True, 'bench_plateau': True, 'protein_target': True, 'elbow_pain': True}  
Paraphrase consistency (legacy): {'tonight': True, 'bench_plateau': True, 'protein_target': True, 'elbow_pain': True}

## Online (machine-scored on real replies)

_Skipped: OPENAI_API_KEY not set; online evals need a real model._

## Judgment-required dimensions

These need a model or human grader (0-2 each): factual_correctness, personalization, correct_use_of_history, evidence_quality, actionable_advice. Rubrics per scenario are in the JSON under `judgment_rubric`.
