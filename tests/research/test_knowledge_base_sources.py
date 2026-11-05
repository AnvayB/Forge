"""Every URL the curated knowledge base can hand to the coach must be a real source.

The coach cites these verbatim, so a storefront or marketing page here becomes a "science"
citation in a reply. This lints the data, not the model.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fitness_coach.research.engine import classify_domain

KB_PATH = Path("config/knowledge_base.md")
URL = re.compile(r"https?://[^\s)>\]]+")
STOREFRONT_FRAGMENTS = ("/products/", "/product/", "/shop", "/store", "/cart", "/collections/")


def _urls() -> list[str]:
    return sorted(set(URL.findall(KB_PATH.read_text(encoding="utf-8"))))


def test_the_knowledge_base_has_sources_to_lint() -> None:
    assert len(_urls()) >= 10


@pytest.mark.parametrize("url", _urls())
def test_every_cited_source_is_a_vetted_domain(url: str) -> None:
    domain_class, reason = classify_domain(url)

    assert domain_class in ("preferred", "educator"), f"{url}: {reason}"


@pytest.mark.parametrize("url", _urls())
def test_no_cited_source_is_a_storefront_page(url: str) -> None:
    assert not any(fragment in url.lower() for fragment in STOREFRONT_FRAGMENTS), url
