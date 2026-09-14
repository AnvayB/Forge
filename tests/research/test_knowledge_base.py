from __future__ import annotations

from pathlib import Path

from fitness_coach.research.knowledge_base import EvidenceTier, KnowledgeBase, infer_evidence_tier


def test_parses_real_knowledge_base() -> None:
    kb = KnowledgeBase(Path("config/knowledge_base.md"))
    entries = kb.entries()
    topics = {entry.topic for entry in entries}
    assert "Progressive Overload" in topics
    assert "Range of Motion: Full vs. Partial" in topics
    overload = next(entry for entry in entries if entry.topic == "Progressive Overload")
    assert len(overload.sources) == 2
    assert all(source.url and source.url.startswith("https://") for source in overload.sources)
    assert overload.strongest_tier == EvidenceTier.SYSTEMATIC_REVIEW


def test_known_urls_match_prompt_builder_whitelist_shape() -> None:
    kb = KnowledgeBase(Path("config/knowledge_base.md"))
    urls = kb.known_urls()
    assert "https://pubmed.ncbi.nlm.nih.gov/27433992/" in urls
    assert all(not url.endswith((")", ".", ",")) for url in urls)


def test_search_finds_relevant_entries_with_synonyms() -> None:
    kb = KnowledgeBase(Path("config/knowledge_base.md"))
    assert kb.search("should I train to failure")[0].topic.startswith("Proximity to Failure")
    assert any("Deload" in e.topic for e in kb.search("do I need a deload"))
    assert any("Shoulder" in e.topic for e in kb.search("bench press shoulder pain"))
    assert any("Range of Motion" in e.topic for e in kb.search("are partial reps ok"))
    assert kb.search("creatine loading phase") == []


def test_tier_inference() -> None:
    assert infer_evidence_tier("A systematic review and meta-analysis") == (
        EvidenceTier.SYSTEMATIC_REVIEW
    )
    assert infer_evidence_tier("randomized within subject design") == EvidenceTier.RCT
    assert infer_evidence_tier("ACSM position stand on protein") == EvidenceTier.POSITION_STAND
    assert infer_evidence_tier("Essentials of Strength Training, 4th ed.") == EvidenceTier.TEXTBOOK
    assert infer_evidence_tier("A Narrative Review") == EvidenceTier.NARRATIVE_REVIEW
    assert infer_evidence_tier("some blog post") == EvidenceTier.UNKNOWN


def test_missing_file_yields_no_entries(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "missing.md")
    assert kb.entries() == []
    assert kb.search("anything") == []
