import pytest

from app.ingestion.fetchers import FETCHERS
from app.ingestion.models import SourceSpec
from app.ingestion.sources import load_sources

SPECS = load_sources("sources/sources.yaml")


def test_catalogue_has_100_plus_sources_across_all_categories():
    assert len(SPECS) >= 100
    assert {s.category for s in SPECS} == {"research", "industry_news", "engineering_blog", "community", "newsletter", "researcher"}


def test_every_kind_has_a_fetcher_and_required_config():
    for s in SPECS:
        assert s.kind in FETCHERS
        if s.kind == "reddit":
            assert s.config.get("subreddit"), s.slug
        if s.kind == "hackernews":
            assert s.config.get("query"), s.slug


def test_urls_are_unique_https():
    assert all(s.url.startswith("https://") for s in SPECS)
    non_hn = [s.url for s in SPECS if s.kind != "hackernews"]  # HN sources share a base URL by design
    assert len(non_hn) == len(set(non_hn))


def test_invalid_slug_rejected():
    with pytest.raises(ValueError):
        SourceSpec(slug="Bad Slug", name="x", category="research", url="https://x.test")


def test_management_catalogue_is_valid_and_does_not_collide_with_the_ai_one():
    from app.config import Settings
    from app.ingestion.sources import load_all_sources

    mgmt = load_sources("sources/management_sources.yaml")
    assert len(mgmt) >= 500 and {s.track for s in mgmt} == {"management"} and all(s.slug.startswith("m-") for s in mgmt)
    assert all(s.url.startswith(("https://", "http://")) for s in mgmt)
    assert all(s.config.get("query") for s in mgmt if s.kind == "hackernews")
    both = load_all_sources(Settings())
    assert len(both) == len(SPECS) + len(mgmt) and {s.track for s in SPECS} == {"ai"}
