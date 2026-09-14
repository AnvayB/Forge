"""Structured view of `config/knowledge_base.md` with deterministic evidence tiers.

The markdown file stays the single curated source of truth (it is still loaded verbatim
into the system prompt). This module parses it into entries so tools can return exact
`Source:` URLs and an evidence tier per source without asking the model to re-judge
study quality on every reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

_URL_PATTERN = re.compile(r"https?://\S+")
_STOPWORDS = frozenset(
    "a an and are as at be by do does for from how i in is it my of on or should that the "
    "this to vs what when why with your you me we our can could would will than more less "
    "about into over under".split()
)


class EvidenceTier(StrEnum):
    """Coarse evidence strength, strongest first."""

    SYSTEMATIC_REVIEW = "systematic_review_or_meta_analysis"
    RCT = "randomized_trial"
    POSITION_STAND = "position_stand_or_consensus"
    TEXTBOOK = "textbook"
    NARRATIVE_REVIEW = "narrative_review"
    PRIMARY_STUDY = "primary_study"
    UNKNOWN = "unknown"


TIER_RANK: dict[EvidenceTier, int] = {
    EvidenceTier.SYSTEMATIC_REVIEW: 6,
    EvidenceTier.RCT: 5,
    EvidenceTier.POSITION_STAND: 4,
    EvidenceTier.TEXTBOOK: 3,
    EvidenceTier.NARRATIVE_REVIEW: 2,
    EvidenceTier.PRIMARY_STUDY: 1,
    EvidenceTier.UNKNOWN: 0,
}


def infer_evidence_tier(text: str) -> EvidenceTier:
    """Infer a tier from citation/title text. Deterministic keyword heuristic."""

    lowered = text.lower()
    if any(
        key in lowered
        for key in (
            "meta-analysis",
            "meta analysis",
            "meta-regression",
            "meta-regressions",
            "systematic review",
            "overview of reviews",
            "umbrella review",
        )
    ):
        return EvidenceTier.SYSTEMATIC_REVIEW
    if any(key in lowered for key in ("randomized", "randomised", "randomized within subject")):
        return EvidenceTier.RCT
    if any(key in lowered for key in ("position stand", "position statement", "consensus")):
        return EvidenceTier.POSITION_STAND
    if any(key in lowered for key in ("narrative review", "review article", "a review")):
        return EvidenceTier.NARRATIVE_REVIEW
    if any(
        key in lowered
        for key in ("essentials of", "human kinetics", " ed.,", " edition", "textbook", "handbook")
    ):
        return EvidenceTier.TEXTBOOK
    if any(key in lowered for key in ("journal", "pubmed", "doi", "peerj", "frontiers", "study")):
        return EvidenceTier.PRIMARY_STUDY
    return EvidenceTier.UNKNOWN


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    citation: str
    url: str | None
    evidence_tier: EvidenceTier

    def as_dict(self) -> dict[str, object]:
        return {
            "citation": self.citation,
            "url": self.url,
            "evidence_tier": self.evidence_tier.value,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    topic: str
    summary: str
    sources: tuple[KnowledgeSource, ...]
    keywords: frozenset[str] = field(default_factory=frozenset)

    @property
    def strongest_tier(self) -> EvidenceTier:
        if not self.sources:
            return EvidenceTier.UNKNOWN
        return max((source.evidence_tier for source in self.sources), key=lambda t: TIER_RANK[t])

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "summary": self.summary,
            "strongest_evidence_tier": self.strongest_tier.value,
            "sources": [source.as_dict() for source in self.sources],
        }


# Query-side synonyms so "bench" finds the shoulder-friendly pressing entry, etc.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "bench": ("press", "pressing", "chest"),
    "press": ("pressing",),
    "pressing": ("press",),
    "rom": ("range", "motion", "partial", "full"),
    "partial": ("range", "motion", "partials"),
    "partials": ("range", "motion", "partial"),
    "failure": ("proximity", "rir", "reserve"),
    "rir": ("failure", "proximity", "reserve"),
    "rpe": ("failure", "proximity", "reserve"),
    "deload": ("variety", "fatigue"),
    "deloads": ("deload", "variety", "fatigue"),
    "plateau": ("overload", "deload", "expectations", "progression", "volume"),
    "plateaus": ("overload", "deload", "expectations", "progression", "volume"),
    "stall": ("overload", "deload", "progression"),
    "stalled": ("overload", "deload", "progression"),
    "volume": ("overload", "sets", "dose"),
    "sets": ("volume", "overload"),
    "frequency": ("week", "times"),
    "tempo": ("technique", "speed"),
    "cheat": ("momentum", "form"),
    "cheating": ("momentum", "form"),
    "momentum": ("cheat", "form"),
    "form": ("technique", "momentum"),
    "shoulder": ("shoulder-friendly", "pressing", "impingement"),
    "shoulders": ("shoulder", "pressing"),
    "knee": ("knee-friendly", "leg", "squat"),
    "knees": ("knee", "leg"),
    "squat": ("knee", "leg"),
    "squats": ("squat", "knee", "leg"),
    "warmup": ("warm-up", "warm", "stretching"),
    "warm": ("warm-up", "stretching"),
    "stretch": ("stretching", "warm-up"),
    "stretching": ("warm-up",),
    "hypertrophy": ("mechanisms", "growth", "muscle"),
    "growth": ("hypertrophy", "muscle", "mechanisms"),
    "pump": ("mechanisms", "hypertrophy"),
    "order": ("ordering", "pre-fatigue", "compound"),
    "ordering": ("order", "pre-fatigue"),
    "isolation": ("ordering", "compound"),
    "compound": ("ordering", "pre-fatigue"),
    "incline": ("pressing", "press", "ordering"),
    "progression": ("overload", "progressive"),
    "overload": ("progressive", "volume"),
    "gains": ("expectations", "hypertrophy"),
    "expectations": ("realistic", "hypertrophy"),
}


# A topic-heading hit (3.0 direct / 1.8 via synonym) or two+ body-keyword hits qualify; a
# single incidental body word (e.g. "loading") does not.
_MIN_MATCH_SCORE = 1.8


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9\-']*", text.lower())
    return {word for word in words if word not in _STOPWORDS and len(word) > 1}


class KnowledgeBase:
    """Parsed, searchable knowledge base. Re-parses on demand so edits need no restart."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[KnowledgeEntry]:
        text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        return _parse_entries(text)

    def known_urls(self) -> set[str]:
        return {source.url for entry in self.entries() for source in entry.sources if source.url}

    def search(self, query: str, *, limit: int = 3) -> list[KnowledgeEntry]:
        """Keyword search with light synonym expansion. Deterministic, in-memory."""

        query_tokens = _tokens(query)
        expanded = set(query_tokens)
        for token in query_tokens:
            expanded.update(_SYNONYMS.get(token, ()))
        if not expanded:
            return []

        scored: list[tuple[float, KnowledgeEntry]] = []
        for entry in self.entries():
            topic_tokens = _tokens(entry.topic)
            score = 0.0
            for token in expanded:
                weight = 1.0 if token in query_tokens else 0.6
                if token in topic_tokens:
                    score += 3.0 * weight
                elif token in entry.keywords:
                    score += 1.0 * weight
            if score >= _MIN_MATCH_SCORE:
                scored.append((score, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _score, entry in scored[:limit]]


def _parse_entries(text: str) -> list[KnowledgeEntry]:
    entries: list[KnowledgeEntry] = []
    sections = re.split(r"^## +", text, flags=re.MULTILINE)[1:]
    for section in sections:
        heading, _, body = section.partition("\n")
        topic = heading.strip()
        body = body.split("\n---", 1)[0]
        source_lines: list[str] = []
        summary_lines: list[str] = []
        current_source: list[str] | None = None
        for raw_line in body.splitlines():
            line = raw_line.rstrip()
            if line.startswith("Source:"):
                if current_source:
                    source_lines.append(" ".join(current_source))
                current_source = [line[len("Source:") :].strip()]
            elif current_source is not None and line.strip():
                current_source.append(line.strip())
            elif current_source is not None and not line.strip():
                source_lines.append(" ".join(current_source))
                current_source = None
            elif line.strip():
                summary_lines.append(line.strip())
        if current_source:
            source_lines.append(" ".join(current_source))

        sources = []
        for citation in source_lines:
            match = _URL_PATTERN.search(citation)
            url = match.group(0).rstrip(").,;") if match else None
            sources.append(
                KnowledgeSource(
                    citation=citation, url=url, evidence_tier=infer_evidence_tier(citation)
                )
            )
        summary = " ".join(summary_lines)
        entries.append(
            KnowledgeEntry(
                topic=topic,
                summary=summary,
                sources=tuple(sources),
                keywords=frozenset(_tokens(topic) | _tokens(summary)),
            )
        )
    return entries
