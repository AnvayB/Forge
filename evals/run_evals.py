"""Run the response-intelligence evals.

    .venv/bin/python -m evals.run_evals --mode offline --label baseline
    OPENAI_API_KEY=... .venv/bin/python -m evals.run_evals --mode online --path both --judge

offline  scores the deterministic router for every scenario (no API key, seconds).
online   seeds a fresh SQLite DB with evals/fixtures.py, asks the real model, and scores
         replies. `--path legacy` uses the pre-routing single-shot behaviour, `--path
         routed` the new pipeline, `--path both` runs each scenario through both so the
         report shows the delta. `--judge` adds a model grader for the judgment rubrics.

Results land in evals/results/<label>.json and <label>.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.fixtures import seed_history
from evals.scoring import (
    JUDGMENT_DIMENSIONS,
    Scenario,
    judgment_rubric,
    llm_judge,
    load_scenarios,
    score_consistency,
    score_offline,
    score_online,
    summarize,
)
from fitness_coach.coach.factory import ServiceFactory
from fitness_coach.coach.service import AnalyticsLockedError
from fitness_coach.config.settings import AppSettings, CoachSettings, load_coach_settings
from fitness_coach.routing.classifier import ALL_CHAT_TOOLS, RouteCategory, RoutingDecision

RESULTS_DIR = Path(__file__).with_name("results")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def legacy_decision(message: str, settings: CoachSettings) -> RoutingDecision:
    """Emulate pre-routing behaviour for offline comparison.

    The old path had no classifier: a keyword gate raised for analytics terms, otherwise
    every message got the full context dump and no tools. Modeled here as LOCKED or a
    JUDGMENT-shaped decision that "needs" all history and exposes nothing.
    """

    lowered = message.lower()
    terms = ("streak", "average", "trend", "monthly", "progress report", "analytics")
    if settings.analytics_locked and any(term in lowered for term in terms):
        return RoutingDecision(
            category=RouteCategory.LOCKED_ANALYTICS,
            needs_conversation_context=False,
            needs_user_history=False,
            needs_calculations=False,
            research_allowed=False,
            stable_knowledge=False,
            tools=(),
            reason="legacy keyword gate",
        )
    return RoutingDecision(
        category=RouteCategory.JUDGMENT,
        needs_conversation_context=False,
        needs_user_history=True,  # everything was dumped into every prompt
        needs_calculations=False,
        research_allowed=False,
        stable_knowledge=True,
        tools=(),
        reason="legacy single-shot: full context dump, no tools",
    )


def run_offline(scenarios: list[Scenario], settings: CoachSettings) -> dict[str, Any]:
    from fitness_coach.routing.classifier import classify_message

    per_scenario: list[dict[str, Any]] = []
    routed: dict[str, RoutingDecision] = {}
    legacy: dict[str, RoutingDecision] = {}
    routed_rows: list[dict[str, bool]] = []
    legacy_rows: list[dict[str, bool]] = []
    for scenario in scenarios:
        decision = classify_message(scenario.message, analytics_locked=settings.analytics_locked)
        old = legacy_decision(scenario.message, settings)
        routed[scenario.id] = decision
        legacy[scenario.id] = old
        r_checks = score_offline(scenario, decision)
        l_checks = score_offline(scenario, old)
        routed_rows.append(r_checks)
        legacy_rows.append(l_checks)
        per_scenario.append(
            {
                "id": scenario.id,
                "category": scenario.category,
                "message": scenario.message,
                "expected_route": scenario.expected_route,
                "routed": {"decision": decision.as_dict(), "checks": r_checks},
                "legacy": {"decision": old.as_dict(), "checks": l_checks},
            }
        )
    return {
        "scenarios": per_scenario,
        "summary": {
            "routed": summarize(routed_rows),
            "legacy": summarize(legacy_rows),
            "consistency_routed": score_consistency(scenarios, routed),
            "consistency_legacy": score_consistency(scenarios, legacy),
        },
    }


def _build_factory(tmp: Path, config_dir: Path, coach_settings: CoachSettings) -> ServiceFactory:
    return ServiceFactory(
        AppSettings(
            database_url=f"sqlite:///{tmp / 'evals.db'}",
            config_dir=config_dir,
            uploads_dir=tmp / "uploads",
            data_dir=tmp / "data",
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
        ),
        coach_settings,
    )


def run_online(
    scenarios: list[Scenario],
    *,
    config_dir: Path,
    coach_settings: CoachSettings,
    paths: list[str],
    judge: bool,
) -> dict[str, Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        return {"skipped": "OPENAI_API_KEY not set; online evals need a real model."}

    rows: dict[str, list[dict[str, bool]]] = {path: [] for path in paths}
    judged: dict[str, list[dict[str, int]]] = {path: [] for path in paths}
    per_scenario: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        factory = _build_factory(tmp, config_dir, coach_settings)
        with factory.session() as session:
            coach = factory.coach_service(session)
            user = coach.get_user("eval-user")
            seed_history(coach, session, user.id)
        judge_client = factory.openai_client().client if judge else None

        for scenario in scenarios:
            entry: dict[str, Any] = {
                "id": scenario.id,
                "category": scenario.category,
                "message": scenario.message,
            }
            for path in paths:
                factory.conversation.clear(user.id)
                with factory.session() as session:
                    coach = factory.coach_service(session)
                    started = datetime.now(UTC)
                    text: str | None = None
                    metadata: dict[str, Any] = {}
                    locked = False
                    error: str | None = None
                    try:
                        response = coach.answer_question(
                            user.id, scenario.message, use_routing=(path == "routed")
                        )
                        text, metadata = response.message, response.metadata
                    except AnalyticsLockedError as exc:
                        locked, text = True, str(exc)
                    except Exception as exc:  # noqa: BLE001 - record and continue
                        error = repr(exc)
                    elapsed = (datetime.now(UTC) - started).total_seconds()
                checks = score_online(
                    scenario, text=text if not locked else None, metadata=metadata, locked=locked
                )
                if error:
                    checks["no_error"] = False
                rows[path].append(checks)
                result: dict[str, Any] = {
                    "reply": text,
                    "route": metadata.get("route"),
                    "tool_calls": metadata.get("tool_calls"),
                    "citations": {
                        k: metadata.get(k)
                        for k in (
                            "unverified_citation_urls",
                            "mislabeled_external_citations",
                            "external_citations",
                        )
                        if metadata.get(k)
                    },
                    "latency_seconds": round(elapsed, 2),
                    "checks": checks,
                    "error": error,
                    "judgment_rubric": judgment_rubric(scenario),
                }
                if judge_client is not None and text and not locked:
                    graded = llm_judge(judge_client, coach_settings.preferred_model, scenario, text)
                    result["judge"] = graded
                    if graded and "scores" in graded:
                        judged[path].append(graded["scores"])
                entry[path] = result
            per_scenario.append(entry)

    summary: dict[str, Any] = {path: summarize(rows[path]) for path in paths}
    for path in paths:
        if judged[path]:
            summary[f"{path}_judgment"] = {
                key: round(sum(s.get(key, 0) for s in judged[path]) / (2 * len(judged[path])), 3)
                for key in JUDGMENT_DIMENSIONS
            }
    return {"scenarios": per_scenario, "summary": summary}


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Eval report: {report['label']}", ""]
    lines.append(f"- commit: `{report['commit']}`  ")
    lines.append(f"- generated: {report['generated_at']}  ")
    lines.append(f"- scenarios: {report['scenario_count']}  ")
    lines.append(f"- mode: {report['mode']}")
    lines.append("")
    offline = report.get("offline")
    if offline:
        lines.append("## Offline (machine-scored, router only)")
        lines.append("")
        lines.append("| check | routed | legacy |")
        lines.append("|---|---|---|")
        keys = sorted(set(offline["summary"]["routed"]) | set(offline["summary"]["legacy"]))
        for key in keys:
            r = offline["summary"]["routed"].get(key, {})
            lg = offline["summary"]["legacy"].get(key, {})
            fr = f"{r.get('passed', 0)}/{r.get('total', 0)} ({r.get('rate', 0):.0%})" if r else "-"
            fl = (
                f"{lg.get('passed', 0)}/{lg.get('total', 0)} ({lg.get('rate', 0):.0%})"
                if lg
                else "-"
            )
            lines.append(f"| {key} | {fr} | {fl} |")
        lines.append("")
        lines.append(
            f"Paraphrase consistency (routed): {offline['summary']['consistency_routed']}  "
        )
        lines.append(f"Paraphrase consistency (legacy): {offline['summary']['consistency_legacy']}")
        lines.append("")
        failures = [s for s in offline["scenarios"] if not all(s["routed"]["checks"].values())]
        if failures:
            lines.append("### Routed failures")
            lines.append("")
            for s in failures:
                bad = [k for k, v in s["routed"]["checks"].items() if not v]
                lines.append(
                    f"- `{s['id']}` ({s['category']}): "
                    f"got `{s['routed']['decision']['category']}`, "
                    f"expected `{s['expected_route']}` — failed {bad}"
                )
            lines.append("")
    online = report.get("online")
    if online:
        lines.append("## Online (machine-scored on real replies)")
        lines.append("")
        if "skipped" in online:
            lines.append(f"_Skipped: {online['skipped']}_")
        else:
            paths = [p for p in ("routed", "legacy") if p in online["summary"]]
            lines.append("| check | " + " | ".join(paths) + " |")
            lines.append("|---|" + "---|" * len(paths))
            keys = sorted({k for p in paths for k in online["summary"][p]})
            for key in keys:
                cells = []
                for p in paths:
                    v = online["summary"][p].get(key)
                    cells.append(f"{v['passed']}/{v['total']} ({v['rate']:.0%})" if v else "-")
                lines.append(f"| {key} | " + " | ".join(cells) + " |")
            for p in paths:
                jkey = f"{p}_judgment"
                if jkey in online["summary"]:
                    lines.append("")
                    lines.append(f"Judgment (model-graded, {p}): {online['summary'][jkey]}")
        lines.append("")
    lines.append("## Judgment-required dimensions")
    lines.append("")
    lines.append(
        "These need a model or human grader (0-2 each): "
        + ", ".join(JUDGMENT_DIMENSIONS)
        + ". Rubrics per scenario are in the JSON under `judgment_rubric`."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=["offline", "online", "both"], default="offline")
    parser.add_argument("--path", choices=["routed", "legacy", "both"], default="both")
    parser.add_argument("--judge", action="store_true", help="model-grade judgment rubrics")
    parser.add_argument("--label", default=None)
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--out-dir", default=str(RESULTS_DIR))
    args = parser.parse_args(argv)

    config_dir = Path(args.config_dir)
    coach_settings = load_coach_settings(config_dir)
    scenarios = load_scenarios()
    commit = _git_commit()
    label = args.label or f"{args.mode}_{commit}_{datetime.now(UTC):%Y%m%d_%H%M%S}"

    report: dict[str, Any] = {
        "label": label,
        "commit": commit,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "scenario_count": len(scenarios),
        "chat_tools": list(ALL_CHAT_TOOLS),
    }
    if args.mode in ("offline", "both"):
        report["offline"] = run_offline(scenarios, coach_settings)
    if args.mode in ("online", "both"):
        paths = ["routed", "legacy"] if args.path == "both" else [args.path]
        report["online"] = run_online(
            scenarios,
            config_dir=config_dir,
            coach_settings=coach_settings,
            paths=paths,
            judge=args.judge,
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{label}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    markdown = _render_markdown(report)
    (out_dir / f"{label}.md").write_text(markdown, encoding="utf-8")
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
