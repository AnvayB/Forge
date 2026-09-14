"""Research decision engine.

Runtime order: safety is decided upstream by the router; here the engine tries the
curated knowledge base first (free, in-memory), and only then - if the route allows it
and a provider is configured - spends a single bounded external search. Source quality
is filtered deterministically by domain before the model ever sees a candidate, and
every accepted external URL is recorded so the citation backstop can verify it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from fitness_coach.research.knowledge_base import (
    TIER_RANK,
    EvidenceTier,
    KnowledgeBase,
    KnowledgeEntry,
    infer_evidence_tier,
)

logger = logging.getLogger(__name__)

EXTERNAL_LABEL = "External (unvetted by curator):"


class ResearchCategory(StrEnum):
    NO_RESEARCH = "no_research_necessary"
    INTERNAL_HISTORY = "internal_user_history_only"
    STABLE_EVIDENCE = "stable_fitness_evidence"
    RECENT_EVIDENCE = "recent_or_current_evidence"
    HIGHER_RISK = "higher_risk_health_query"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(slots=True)
class SourceCandidate:
    url: str
    title: str = ""
    snippet: str = ""
    evidence_tier: EvidenceTier = EvidenceTier.UNKNOWN
    domain_class: str = "unknown"

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc.lower().removeprefix("www.")

    def as_dict(self) -> dict[str, object]:
        return {
            "label": EXTERNAL_LABEL,
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet[:300],
            "evidence_tier": self.evidence_tier.value,
            "domain_class": self.domain_class,
        }


class ResearchProvider(Protocol):
    """Minimal external search interface. One call, bounded results, no fetch stage."""

    available: bool

    def search(self, query: str, *, max_results: int = 5) -> list[SourceCandidate]: ...


class NullResearchProvider:
    """Default provider: external research is not configured."""

    available = False

    def search(self, query: str, *, max_results: int = 5) -> list[SourceCandidate]:
        return []


class OpenAIWebSearchProvider:
    """External search via the OpenAI Responses API built-in `web_search` tool.

    Reuses the already-configured OpenAI credentials, so no new secret is needed. A
    per-call timeout keeps the whole reply inside the agreed ~10s budget.
    """

    def __init__(self, client: Any, model: str, *, timeout_seconds: float = 8.0) -> None:
        self._client = client
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return self._client is not None

    def search(self, query: str, *, max_results: int = 5) -> list[SourceCandidate]:
        if self._client is None:
            return []
        prompt = (
            "Find the strongest available evidence on the following resistance-training or "
            "nutrition question. Prefer systematic reviews, meta-analyses, randomized trials, "
            "and position stands from ACSM, NSCA, or ISSN, or evidence summaries from Stronger "
            "by Science, Renaissance Periodization, or Jeff Nippard. Cite the sources.\n\n"
            f"Question: {query}"
        )
        try:
            response = self._client.with_options(timeout=self.timeout_seconds).responses.create(
                model=self.model,
                tools=[{"type": "web_search"}],
                input=prompt,
            )
        except Exception as error:  # noqa: BLE001 - external call; degrade to "no evidence"
            logger.warning("external_research_failed error=%s", error)
            return []
        return _candidates_from_response(response, max_results=max_results)


def _candidates_from_response(response: Any, *, max_results: int) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    seen: set[str] = set()
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            text = getattr(part, "text", "") or ""
            for annotation in getattr(part, "annotations", None) or []:
                if getattr(annotation, "type", None) != "url_citation":
                    continue
                url = str(getattr(annotation, "url", "")).rstrip(").,;")
                if not url or url in seen:
                    continue
                seen.add(url)
                start = max(0, int(getattr(annotation, "start_index", 0) or 0) - 240)
                end = int(getattr(annotation, "end_index", 0) or 0) or len(text)
                candidates.append(
                    SourceCandidate(
                        url=url,
                        title=str(getattr(annotation, "title", "") or ""),
                        snippet=text[start:end].strip(),
                    )
                )
                if len(candidates) >= max_results:
                    return candidates
    return candidates


# --- deterministic source-quality policy ---------------------------------------------

_PREFERRED_DOMAINS = (
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "doi.org",
    "nature.com",
    "sciencedirect.com",
    "link.springer.com",
    "springer.com",
    "onlinelibrary.wiley.com",
    "tandfonline.com",
    "peerj.com",
    "frontiersin.org",
    "jissn.biomedcentral.com",
    "biomedcentral.com",
    "journals.lww.com",
    "journals.humankinetics.com",
    "journal.iusca.org",
    "mdpi.com",
    "bmj.com",
    "bjsm.bmj.com",
    "thelancet.com",
    "jamanetwork.com",
    "sportsmedicine-open.springeropen.com",
    "academic.oup.com",
    "journals.physiology.org",
    "physoc.onlinelibrary.wiley.com",
    "cochranelibrary.com",
    "acsm.org",
    "nsca.com",
    "journals.sagepub.com",
    "cambridge.org",
    "jstage.jst.go.jp",
    "revistas.rcaap.pt",
    "scielo.br",
    "europepmc.org",
    "sportrxiv.org",
    "osf.io",
)
_EDUCATOR_DOMAINS = (
    "strongerbyscience.com",
    "rpstrength.com",
    "renaissanceperiodization.com",
    "jeffnippard.com",
    "examine.com",
    "macrofactorapp.com",
    "jpgcoaching.com",
)
_REJECTED_DOMAIN_PATTERNS = (
    "reddit.com",
    "quora.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "pinterest.",
    "medium.com",
    "bodybuilding.com/forum",
    "forum.",
    "forums.",
    "t-nation.com",
    "menshealth.com",
    "womenshealthmag.com",
    "healthline.com",
    "webmd.com",
    "verywellfit.com",
    "livestrong.com",
    "muscleandfitness.com",
    "muscleandstrength.com",
    "bodybuilding.com",
    "gnc.com",
    "myprotein.com",
    "transparentlabs.com",
    "optimumnutrition.com",
    "prnewswire.com",
    "businesswire.com",
    "eurekalert.org",
    "sciencedaily.com",
    "wikipedia.org",
)


def classify_domain(url: str) -> tuple[str, str]:
    """Return (class, reason) for a URL: preferred | educator | rejected | unknown."""

    domain = urlparse(url).netloc.lower().removeprefix("www.")
    path = urlparse(url).path.lower()
    full = f"{domain}{path}"
    for pattern in _REJECTED_DOMAIN_PATTERNS:
        if pattern in full:
            return "rejected", f"matches rejected source pattern '{pattern}'"
    for preferred in _PREFERRED_DOMAINS:
        if domain == preferred or domain.endswith("." + preferred):
            return "preferred", "peer-reviewed venue or professional body"
    for educator in _EDUCATOR_DOMAINS:
        if domain == educator or domain.endswith("." + educator):
            return "educator", "named evidence-based educator/aggregator"
    if domain.startswith("journal") or ".edu" in domain or "journal" in domain:
        return "preferred", "journal-like domain"
    return "unknown", "unrecognized domain; not verifiably peer-reviewed or a professional body"


@dataclass(slots=True)
class ResearchResult:
    category: ResearchCategory
    topic: str
    kb_entries: list[KnowledgeEntry] = field(default_factory=list)
    external_sources: list[SourceCandidate] = field(default_factory=list)
    rejected_sources: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    searched_externally: bool = False

    @property
    def external_urls(self) -> set[str]:
        return {source.url for source in self.external_sources}

    @property
    def strongest_tier(self) -> EvidenceTier:
        tiers = [entry.strongest_tier for entry in self.kb_entries] + [
            source.evidence_tier for source in self.external_sources
        ]
        if not tiers:
            return EvidenceTier.UNKNOWN
        return max(tiers, key=lambda tier: TIER_RANK[tier])

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "topic": self.topic,
            "strongest_evidence_tier": self.strongest_tier.value,
            "knowledge_base_entries": [entry.as_dict() for entry in self.kb_entries],
            "external_sources": [source.as_dict() for source in self.external_sources],
            "rejected_sources": list(self.rejected_sources),
            "notes": list(self.notes),
            "searched_externally": self.searched_externally,
        }


class ResearchEngine:
    """Decides how much evidence gathering a topic warrants, then gathers it."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        provider: ResearchProvider | None = None,
        *,
        candidates_path: Path | None = None,
        max_external_results: int = 5,
        max_accepted_sources: int = 2,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.provider: ResearchProvider = provider or NullResearchProvider()
        self.candidates_path = candidates_path
        self.max_external_results = max_external_results
        self.max_accepted_sources = max_accepted_sources

    def investigate(
        self, topic: str, *, risk: bool = False, allow_external: bool = True
    ) -> ResearchResult:
        entries = self.knowledge_base.search(topic)
        if entries:
            category = ResearchCategory.HIGHER_RISK if risk else ResearchCategory.STABLE_EVIDENCE
            result = ResearchResult(category=category, topic=topic, kb_entries=entries)
            if risk:
                result.notes.append(
                    "Health-adjacent question: pair guidance with an in-person evaluation "
                    "recommendation when pain is recurring, worsening, or acute."
                )
            return result

        if risk:
            return ResearchResult(
                category=ResearchCategory.HIGHER_RISK,
                topic=topic,
                notes=[
                    "No curated coverage; do not use external web research for a health-adjacent "
                    "question. Answer from general consensus and recommend evaluation if warranted."
                ],
            )

        if not allow_external or not self.provider.available:
            reason = (
                "external research not permitted for this route"
                if not allow_external
                else "no external research provider configured"
            )
            return ResearchResult(
                category=ResearchCategory.INSUFFICIENT_EVIDENCE,
                topic=topic,
                notes=[f"Knowledge base has no coverage and {reason}; say so plainly."],
            )

        candidates = self.provider.search(topic, max_results=self.max_external_results)
        result = ResearchResult(
            category=ResearchCategory.INSUFFICIENT_EVIDENCE, topic=topic, searched_externally=True
        )
        for candidate in candidates:
            domain_class, reason = classify_domain(candidate.url)
            candidate.domain_class = domain_class
            candidate.evidence_tier = infer_evidence_tier(f"{candidate.title} {candidate.snippet}")
            if domain_class in ("preferred", "educator"):
                if len(result.external_sources) < self.max_accepted_sources:
                    result.external_sources.append(candidate)
            else:
                result.rejected_sources.append({"url": candidate.url, "reason": reason})

        if result.external_sources:
            result.category = ResearchCategory.RECENT_EVIDENCE
            if len(result.external_sources) == 1:
                result.notes.append(
                    "Only one qualifying source was found; present it as limited evidence, "
                    "not settled consensus."
                )
            tiers = {source.evidence_tier for source in result.external_sources}
            if EvidenceTier.SYSTEMATIC_REVIEW not in tiers and EvidenceTier.RCT not in tiers:
                result.notes.append(
                    "No synthesis-level source found; treat findings as preliminary."
                )
            self._record_candidates(topic, result.external_sources)
        else:
            result.notes.append(
                "External search returned no source meeting the quality bar; communicate "
                "uncertainty rather than citing a weak source."
            )
        return result

    def _record_candidates(self, topic: str, sources: list[SourceCandidate]) -> None:
        if self.candidates_path is None:
            return
        try:
            self.candidates_path.parent.mkdir(parents=True, exist_ok=True)
            with self.candidates_path.open("a", encoding="utf-8") as handle:
                for source in sources:
                    handle.write(
                        json.dumps(
                            {
                                "recorded_at": datetime.now(UTC).isoformat(),
                                "topic": topic,
                                "url": source.url,
                                "title": source.title,
                                "evidence_tier": source.evidence_tier.value,
                            }
                        )
                        + "\n"
                    )
        except OSError as error:
            logger.warning("kb_candidate_record_failed error=%s", error)


def format_research_for_prompt(result: ResearchResult) -> str:
    """Render a research result as a compact, labeled block for the model."""

    lines = [f"Research category: {result.category.value}"]
    if result.kb_entries:
        lines.append("Curated knowledge base entries (cite URLs exactly, unlabeled):")
        for entry in result.kb_entries:
            lines.append(f"- {entry.topic} [{entry.strongest_tier.value}]: {entry.summary}")
            for source in entry.sources:
                lines.append(f"  Source: {source.citation}")
    if result.external_sources:
        lines.append(
            "External sources (NOT in the curated knowledge base - every citation of these "
            f"must be prefixed with '{EXTERNAL_LABEL}'):"
        )
        for source in result.external_sources:
            lines.append(
                f"- [{source.evidence_tier.value}] {source.title or source.url} — {source.url}"
            )
            if source.snippet:
                lines.append(f"  Snippet: {source.snippet[:280]}")
    for note in result.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)
