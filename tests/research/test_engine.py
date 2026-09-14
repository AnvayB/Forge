from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fitness_coach.research.engine import (
    EXTERNAL_LABEL,
    NullResearchProvider,
    OpenAIWebSearchProvider,
    ResearchCategory,
    ResearchEngine,
    SourceCandidate,
    classify_domain,
    format_research_for_prompt,
)
from fitness_coach.research.knowledge_base import EvidenceTier, KnowledgeBase

KB = KnowledgeBase(Path("config/knowledge_base.md"))


class _Provider:
    available = True

    def __init__(self, candidates: list[SourceCandidate]) -> None:
        self.candidates = candidates
        self.queries: list[str] = []

    def search(self, query: str, *, max_results: int = 5) -> list[SourceCandidate]:
        self.queries.append(query)
        return self.candidates[:max_results]


def test_kb_hit_is_stable_evidence_without_external_search() -> None:
    provider = _Provider([SourceCandidate(url="https://pubmed.ncbi.nlm.nih.gov/1/")])
    engine = ResearchEngine(KB, provider)
    result = engine.investigate("training to failure vs RIR")
    assert result.category == ResearchCategory.STABLE_EVIDENCE
    assert result.kb_entries
    assert provider.queries == []
    assert result.searched_externally is False


def test_no_provider_means_insufficient_evidence() -> None:
    engine = ResearchEngine(KB, NullResearchProvider())
    result = engine.investigate("creatine loading phase")
    assert result.category == ResearchCategory.INSUFFICIENT_EVIDENCE
    assert result.external_sources == []
    assert "no external research provider" in result.notes[0]


def test_external_sources_are_filtered_and_labeled(tmp_path: Path) -> None:
    provider = _Provider(
        [
            SourceCandidate(url="https://www.reddit.com/r/fitness/creatine", title="reddit thread"),
            SourceCandidate(
                url="https://pubmed.ncbi.nlm.nih.gov/12345/",
                title="Creatine supplementation: a systematic review and meta-analysis",
            ),
            SourceCandidate(url="https://someblog.example/creatine", title="Top 10 creatine tips"),
            SourceCandidate(
                url="https://jissn.biomedcentral.com/articles/1",
                title="ISSN position stand: creatine",
            ),
        ]
    )
    engine = ResearchEngine(KB, provider, candidates_path=tmp_path / "kb_candidates.jsonl")
    result = engine.investigate("creatine loading phase")
    assert result.category == ResearchCategory.RECENT_EVIDENCE
    assert [s.url for s in result.external_sources] == [
        "https://pubmed.ncbi.nlm.nih.gov/12345/",
        "https://jissn.biomedcentral.com/articles/1",
    ]
    assert result.external_sources[0].evidence_tier == EvidenceTier.SYSTEMATIC_REVIEW
    assert result.external_sources[1].evidence_tier == EvidenceTier.POSITION_STAND
    assert {r["url"] for r in result.rejected_sources} == {
        "https://www.reddit.com/r/fitness/creatine",
        "https://someblog.example/creatine",
    }
    assert result.strongest_tier == EvidenceTier.SYSTEMATIC_REVIEW
    recorded = [
        json.loads(line) for line in (tmp_path / "kb_candidates.jsonl").read_text().splitlines()
    ]
    assert len(recorded) == 2
    rendered = format_research_for_prompt(result)
    assert EXTERNAL_LABEL in rendered
    assert "https://pubmed.ncbi.nlm.nih.gov/12345/" in rendered


def test_single_weak_source_is_flagged_as_limited() -> None:
    provider = _Provider(
        [SourceCandidate(url="https://www.strongerbyscience.com/x", title="An article on X")]
    )
    result = ResearchEngine(KB, provider).investigate("some new modality")
    assert result.category == ResearchCategory.RECENT_EVIDENCE
    assert any("Only one qualifying source" in note for note in result.notes)
    assert any("No synthesis-level source" in note for note in result.notes)


def test_risk_blocks_external_search() -> None:
    provider = _Provider([SourceCandidate(url="https://pubmed.ncbi.nlm.nih.gov/1/")])
    result = ResearchEngine(KB, provider).investigate("elbow tendon pain", risk=True)
    assert result.category == ResearchCategory.HIGHER_RISK
    assert provider.queries == []


def test_route_can_forbid_external_search() -> None:
    provider = _Provider([SourceCandidate(url="https://pubmed.ncbi.nlm.nih.gov/1/")])
    result = ResearchEngine(KB, provider).investigate("zzz unknown", allow_external=False)
    assert result.category == ResearchCategory.INSUFFICIENT_EVIDENCE
    assert provider.queries == []


def test_classify_domain() -> None:
    assert classify_domain("https://pubmed.ncbi.nlm.nih.gov/1/")[0] == "preferred"
    assert classify_domain("https://www.strongerbyscience.com/a")[0] == "educator"
    assert classify_domain("https://www.reddit.com/r/x")[0] == "rejected"
    assert classify_domain("https://www.myprotein.com/blog")[0] == "rejected"
    assert classify_domain("https://random-fitness-site.com/post")[0] == "unknown"


def test_openai_provider_parses_url_citations() -> None:
    annotation = SimpleNamespace(
        type="url_citation",
        url="https://pubmed.ncbi.nlm.nih.gov/999/",
        title="Study",
        start_index=0,
        end_index=20,
    )
    part = SimpleNamespace(text="Some evidence text here", annotations=[annotation])
    message = SimpleNamespace(type="message", content=[part])
    response = SimpleNamespace(output=[SimpleNamespace(type="web_search_call"), message])

    class _Responses:
        def create(self, **kwargs: object) -> SimpleNamespace:
            assert kwargs["tools"] == [{"type": "web_search"}]
            return response

    class _Client:
        responses = _Responses()

        def with_options(self, **kwargs: object) -> _Client:
            return self

    provider = OpenAIWebSearchProvider(_Client(), "test-model")
    assert provider.available is True
    candidates = provider.search("anything")
    assert [c.url for c in candidates] == ["https://pubmed.ncbi.nlm.nih.gov/999/"]
    assert candidates[0].title == "Study"


def test_openai_provider_degrades_on_error() -> None:
    class _Client:
        def with_options(self, **kwargs: object) -> _Client:
            raise RuntimeError("boom")

    assert OpenAIWebSearchProvider(_Client(), "m").search("q") == []
    assert OpenAIWebSearchProvider(None, "m").available is False
