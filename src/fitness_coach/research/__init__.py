"""Research and evidence layer: curated knowledge base first, bounded external search second."""

from fitness_coach.research.engine import (
    NullResearchProvider,
    OpenAIWebSearchProvider,
    ResearchCategory,
    ResearchEngine,
    ResearchResult,
    SourceCandidate,
)
from fitness_coach.research.knowledge_base import (
    EvidenceTier,
    KnowledgeBase,
    KnowledgeEntry,
    KnowledgeSource,
    infer_evidence_tier,
)

__all__ = [
    "EvidenceTier",
    "KnowledgeBase",
    "KnowledgeEntry",
    "KnowledgeSource",
    "NullResearchProvider",
    "OpenAIWebSearchProvider",
    "ResearchCategory",
    "ResearchEngine",
    "ResearchResult",
    "SourceCandidate",
    "infer_evidence_tier",
]
