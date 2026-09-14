# Fitness Coach — Intelligence Systems Guide

This guide covers the response-intelligence layers added on top of the original bot:
how a free-text message is routed, what the coach can look up, when it researches, how it
cites, and how to measure whether a change made it better. The original command reference
in [`bot_guide.md`](bot_guide.md) still applies; the `!` commands are repeated below for
convenience.

## 1. How a message is answered now

```
message ──▶ router (deterministic) ──▶ prompt = static config + routing guidance
                │                                 + recent turns (if needed)
                │
                ├─ locked_analytics ──▶ redirect to !progress (no model call)
                │
                └─ everything else ──▶ model + a small, route-specific tool set
                                         │
                                         ├─ tools: your logged data, today's plan,
                                         │         targets, knowledge base, research
                                         │
                                         └─ reply ──▶ citation check ──▶ Discord
```

**Router** (`src/fitness_coach/routing/classifier.py`). Runs before any model call and
never generates text. It classifies the message and decides, explicitly, which sources the
reply needs and which tools the model may call. Priority order:

| Route | Fires on | Tools exposed | Research |
|---|---|---|---|
| `safety` | pain/injury words near a body part, red-flag symptoms, "should I see a physio", recalling a known issue | constraints, exercise history, recent events, knowledge base, `log_injury`, `remember_fact` | never |
| `exercise_history` | a named lift + progress/plateau/history language ("why is my bench stuck") | exercise history, constraints, recent events, knowledge base | no |
| `locked_analytics` | streaks, averages, adherence, monthly trends about *you* | none — redirected to the review | no |
| `nutrition_targets` | "how much protein", "my macro targets" | nutrition targets, knowledge base | no |
| `schedule` | "what should I train tonight", "is tomorrow a rest day", moving a day | today's plan, constraints, recent events | no |
| `recent_activity` | "what did I do yesterday", "how did I sleep", commitments | recent events, constraints (+ exercise history if a lift is named) | no |
| `conversational` | thanks / ok / hi | none | no |
| `knowledge` | general questions ("why do we do incline first", "does failure build more muscle") | knowledge base, external research, constraints | allowed |
| `judgment` | "should I…", tradeoffs, short follow-ups, anything personal and unclassified | all chat tools | allowed |

Safety always wins. The analytics lock is **structural**: no chat tool computes streaks,
adherence, or multi-week averages, so no phrasing can reach them outside the scheduled
review. Use `!route <message>` to see exactly how any message would be classified.

**Tools** (`src/fitness_coach/coach/tools.py`). Each returns JSON with `raw` (records as
logged) and `derived` (numbers computed by code — Epley 1RM, stall streaks, baseline
verdicts). The model narrates; it does not compute.

| Tool | Returns |
|---|---|
| `get_recent_events(event_type, days≤14)` | raw workouts / cardio / nutrition / sleep / measurements |
| `get_exercise_history(exercise_name, days≤120)` | per-session sets, top set, est. 1RM, consecutive non-improving sessions, `stalled`, baseline verdict |
| `get_active_constraints()` | active injuries, exercises to avoid, active `!adjust` overrides, open commitments, remembered facts |
| `get_todays_plan(date?)` | weekday split and exercise list from `training_preferences.md`, cardio day flag, overrides, last workout |
| `get_nutrition_targets()` | calorie/macro targets from `coach_settings.yaml` and today's logged totals |
| `search_knowledge_base(topic)` | matching `knowledge_base.md` entries with exact Source URLs and an evidence tier |
| `flag_for_external_research(topic)` | one bounded external search (see §2) |
| `remember_fact(key, value)` | durable fact in `conversation_memory` |
| `log_injury(body_area, description, severity)` | create/refresh an `injury_history` record |

**Conversation window** (`coach/conversation.py`). The last 8 turns of the current session
(2-hour expiry) are shown to the model only on routes that need them (safety, judgment,
follow-ups), so "about a week, dull ache" after "my elbow hurts" works. Durable memory is
still structured data — commitments, injuries, `!adjust`, remembered facts.

## 2. Research and citations

Order of preference, enforced in `src/fitness_coach/research/engine.py`:

1. **Curated knowledge base** (`config/knowledge_base.md`) — searched in memory, free.
   Citations reproduce the `Source:` URL exactly and appear unlabeled.
2. **External search** — only on `knowledge`/`judgment` routes, only when the knowledge
   base has no coverage, at most **one** search per reply, using the OpenAI built-in
   `web_search` tool with the same API key (8s timeout). Candidates are filtered
   deterministically by domain: peer-reviewed venues, ACSM/NSCA/ISSN, and named
   evidence-based educators pass; forums, influencer blogs, press releases, supplement
   vendors, and unrecognised domains are rejected. Anything cited from here is prefixed
   **`External (unvetted by curator):`**.
3. **Insufficient evidence** — if nothing clears the bar, the coach says so instead of
   citing something weak. Pain/injury questions never use external search.

Every reply's URLs are checked after generation (`CoachService.citation_report`):
in the knowledge base → fine; fetched this turn → must carry the external label;
neither → flagged as `unverified_citation_urls` in the response metadata and logged.
Accepted external sources are appended to `data/kb_candidates.jsonl` so you can promote
good ones into `knowledge_base.md` by hand.

Turn external research off with `external_research_enabled: false` in
`config/coach_settings.yaml`.

## 3. Coaching voice

The rules live in `config/system_prompt.md` under **Response Policy** and **Evidence
Synthesis** (summarised in `coach_principles.md`). In short: answer first, short by
default, never narrate retrieval ("I see that…"), surface history only when it changes the
answer, no bullet lists for conversational replies, confidence proportional to evidence,
one targeted question when a detail would change the advice, and pushback scaled to the
stakes (preference → lean and defer; contradicts their own data → lead with the evidence;
safety → don't soften it).

## 4. `!` commands

| Command | Example | What it does |
|---|---|---|
| `!checkin` | `!checkin` | Accountability nudge — today's plan and open commitments |
| `!workout` | `!workout bicep curl: 30lbs x10, 35lbs x5` or attach screenshots | Logs a workout; typed sets are parsed; new PRs and baseline verdicts are reported |
| `!cardio` | `!cardio 35 incline walk` | Logs cardio — minutes, then modality |
| `!nutrition` | `!nutrition 129g -1g 90g 814cals` | Logs carbs, fat, protein, calories **remaining** (MyFitnessPal order) |
| `!sleep` | `!sleep 6h38m` or `!sleep 398` | Logs sleep as a duration or minutes |
| `!commit` | `!commit I'll do cardio tomorrow morning` | Saves a commitment the coach follows up on |
| `!adjust` | `!adjust 2 Shift chest/back to Monday this week` | Temporary schedule override for N days; the router's `get_todays_plan` honours it |
| `!setbaseline` | `!setbaseline Bench Press 135 185` | Sets the baseline (and optional max) each logged set is judged against |
| `!baselines` | `!baselines` | Lists configured baselines |
| `!progress` | `!progress` | The periodic review — the only place cumulative analytics appear |
| `!lastreview` | `!lastreview` | Re-sends the most recent review |
| `!recent` | `!recent 10` | Raw recent entries straight from the database |
| `!route` | `!route why has my bench not moved in a month` | **New.** Shows how the router would classify a message: route, tools, research allowed, matched terms. Nothing is sent to the model. |

Free text (no `!`) goes through the router. Examples and what happens:

| You type | Route | What the coach does |
|---|---|---|
| `What should I train tonight?` | schedule | looks up Tuesday → Chest + Back + Shoulders, applies any `!adjust`, lists the exercises |
| `Why has my bench not progressed in 5 weeks?` | exercise_history | pulls your bench sessions; reports the real pattern (e.g. 185×5 for four sessions) and one next step |
| `How much protein should I eat?` | nutrition_targets | answers from your configured 160 g — no history, no lecture |
| `My elbow hurts when I curl.` | safety | records the report, asks duration/severity or gives a concrete modification; escalates only for recurring/sharp pain |
| `Is creatine worth taking?` | knowledge | knowledge base first; may do one labeled external search |
| `What's my workout streak?` | locked_analytics | "locked until your review — next unlocks on …" |
| `ok sounds good` | conversational | one short line, no tools |

## 5. Evals

```bash
# router-only checks, no API key, ~10 s
.venv/bin/python -m evals.run_evals --mode offline --label my_change

# real replies, scored + compared against the pre-routing path
OPENAI_API_KEY=... .venv/bin/python -m evals.run_evals --mode online --path both --judge --label my_change
```

Reports land in `evals/results/<label>.{json,md}`. `evals/scenarios.yaml` holds 72
scenarios across workout programming, exercise selection, plateaus, nutrition, cardio,
adherence, historical context, ambiguous, evidence-heavy, health-adjacent, and adversarial
isolation (irrelevant history must not leak). `evals/fixtures.py` seeds the same user
history every run.

Machine-scored dimensions: route correctness, tool exposure/selection, history isolation,
research necessity, safety gate, paraphrase consistency, hallucinated citations,
over-citation, conciseness, forbidden phrases (retrieval narration, disclaimers, clichés),
history leaks, required mentions, external labeling, safety boundary language.

Judgment dimensions (model or human, rubric per scenario in the JSON): factual
correctness, personalization, correct use of history, evidence quality, actionable
advice. `--judge` runs a model grader.

The committed baseline is `evals/results/baseline_offline.md`. The offline suite also
runs in CI (`tests/evals/`) and fails if router accuracy drops below 95% or any
isolation/safety check regresses.

## 6. Configuration knobs (`config/coach_settings.yaml`)

| Key | Default | Effect |
|---|---|---|
| `analytics_locked` | `true` | keep cumulative stats out of chat (structural; also the legacy keyword gate) |
| `external_research_enabled` | `true` | allow the one-per-reply external search on knowledge/judgment routes |
| `external_research_timeout_seconds` | `8.0` | per-search timeout so replies stay under ~10 s |
| `max_tool_rounds` | `4` | function-calling rounds per reply |

## 7. Known limits

- The router is pattern-based. It is inspectable and tested against 72 scenarios, but new
  phrasings can misroute; the fallthrough is `judgment` (full tools), so the cost of a miss
  is an extra tool call rather than a wrong lock decision.
- External research returns search snippets, not full papers; evidence tiers are inferred
  from titles/snippets by keyword. Treat labeled external citations as leads.
- The conversation window is process-local (lost on restart) by design.
- The live OpenAI tool-calling loop is tested with stubs; run the online evals once with a
  real key after upgrading the `openai` package.
