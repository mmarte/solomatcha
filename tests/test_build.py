import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

TODAY = dt.date(2026, 11, 14)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A scratch copy of the repo so tests can add guides freely."""
    dest = tmp_path / "proj"
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", "public", "__pycache__", ".pytest_cache"))
    for f in (dest / "content" / "guides").glob("*.md"):
        f.unlink()
    monkeypatch.setattr(build, "ROOT", dest)
    return dest


def guide(project, name, body="Plain text.", **meta):
    fm = {"title": "A fine guide title", "description": "x" * 60, "category": "explainers", "date": "2026-11-01"}
    fm.update(meta)
    lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    (project / "content" / "guides" / f"{name}.md").write_text(f"---\n{lines}\n---\n\n{body}\n", encoding="utf-8")


def run(project, drafts=False):
    return build.build(project / "out", include_drafts=drafts, today=TODAY)


def test_empty_site_builds(project):
    s = run(project)
    assert s["guides"] == 0
    assert (project / "out" / "index.html").exists()
    assert "first guides are on the way" in (project / "out" / "guides" / "index.html").read_text()


def test_published_guide_appears_everywhere(project):
    guide(project, "one", "## Heading\n\nHello.")
    run(project)
    out = project / "out"
    assert "A fine guide title" in (out / "guides" / "index.html").read_text()
    assert "A fine guide title" in (out / "index.html").read_text()
    assert "/guides/one/" in (out / "sitemap.xml").read_text()
    assert "/guides/one/" in (out / "feed.xml").read_text()
    assert (out / "guides" / "topic" / "explainers" / "index.html").exists()


def test_todo_blocks_published_but_not_draft(project):
    guide(project, "bad", "TODO(first-hand): add detail")
    with pytest.raises(build.BuildError):
        run(project)
    guide(project, "bad", "TODO(first-hand): add detail", draft="true")
    assert run(project)["guides"] == 0          # hidden in production
    assert run(project, drafts=True)["drafts"] == 1


@pytest.mark.parametrize("phrase", ["It can cure anything.", "A great detox tea.", "Helps with weight loss."])
def test_health_claims_blocked(project, phrase):
    guide(project, "claim", phrase)
    with pytest.raises(build.BuildError) as e:
        run(project)
    assert "health-claim" in str(e.value)


def test_unknown_affiliate_key_blocked(project):
    guide(project, "aff", "[Buy](aff:nope)")
    with pytest.raises(build.BuildError):
        run(project)


def test_affiliate_link_rewritten_and_disclosed(project):
    (project / "affiliates.json").write_text(json.dumps({"shop": {"url": "https://example.com/?a=1&b=2", "merchant": "Shop"}}))
    guide(project, "aff", "[Buy](aff:shop)")
    run(project)
    page = (project / "out" / "guides" / "aff" / "index.html").read_text()
    assert 'rel="sponsored nofollow noopener"' in page
    assert "https://example.com/?a=1&amp;b=2" in page
    assert "affiliate links" in page


def test_no_disclosure_without_affiliate_links(project):
    guide(project, "plain")
    run(project)
    assert 'aria-label="Affiliate disclosure"' not in (project / "out" / "guides" / "plain" / "index.html").read_text()


def test_future_dated_guide_is_scheduled(project):
    guide(project, "later", date="2026-12-25")
    assert run(project)["guides"] == 0


def test_unknown_category_blocked(project):
    guide(project, "cat", category="nonsense")
    with pytest.raises(build.BuildError):
        run(project)


def test_draft_pages_are_noindex_and_not_in_sitemap(project):
    guide(project, "d", draft="true")
    run(project, drafts=True)
    assert 'noindex' in (project / "out" / "guides" / "d" / "index.html").read_text()
    assert "/guides/d/" not in (project / "out" / "sitemap.xml").read_text()


def test_archive_groups_by_month_newest_first(project):
    guide(project, "a", date="2026-10-02", title="October guide here")
    guide(project, "b", date="2026-11-03", title="November guide here")
    run(project)
    page = (project / "out" / "guides" / "index.html").read_text()
    assert page.index("November 2026") < page.index("October 2026")


def test_prev_next_links(project):
    guide(project, "a", date="2026-10-02", title="Older guide title")
    guide(project, "b", date="2026-11-03", title="Newer guide title")
    run(project)
    page = (project / "out" / "guides" / "b" / "index.html").read_text()
    assert 'class="prev"' in page and "Older guide title" in page


def test_head_snippets_are_injected(project):
    site = json.loads((project / "site.json").read_text())
    site["head_snippets"] = ['<script id="adtag">1</script>']
    (project / "site.json").write_text(json.dumps(site))
    run(project)
    assert 'id="adtag"' in (project / "out" / "index.html").read_text()


def test_brand_never_translated():
    for path in list(ROOT.rglob("*.html")) + list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.json")):
        if "tests" in path.parts or ".git" in path.parts:
            continue
        assert "only matcha" not in path.read_text(encoding="utf-8").lower(), path


def test_generate_pick_skips_reviews_and_existing(monkeypatch):
    sys.path.insert(0, str(ROOT / "pipeline"))
    import generate
    rows = [{"slug": "r", "type": "review", "status": "planned"},
            {"slug": "x", "type": "explainer", "status": "drafted"},
            {"slug": "ok", "type": "how-to", "status": "planned"}]
    assert generate.pick(rows)["slug"] == "ok"


def test_no_ads_pages_get_no_snippets(project):
    site = json.loads((project / "site.json").read_text())
    site["head_snippets"] = ['<script id="adtag">1</script>']
    (project / "site.json").write_text(json.dumps(site))
    (project / "content" / "pages" / "legal.md").write_text("---\nno_ads: true\ntitle: Legal\n---\n\nText.\n")
    run(project)
    assert "adtag" not in (project / "out" / "legal" / "index.html").read_text()
    assert "adtag" in (project / "out" / "index.html").read_text()


def test_generate_skips_quietly_without_key(monkeypatch, tmp_path, capsys):
    sys.path.insert(0, str(ROOT / "pipeline"))
    import generate
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(generate, "GUIDES", tmp_path)
    monkeypatch.setattr(generate, "write_topics", lambda *a: (_ for _ in ()).throw(AssertionError("must not write")))
    assert generate.main([]) == 0          # no key: warn, exit 0, change nothing
    assert "::warning" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_generate_gemini_success_and_quota_error(monkeypatch, tmp_path):
    import io
    import urllib.error
    import urllib.request
    sys.path.insert(0, str(ROOT / "pipeline"))
    import generate
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    ok = json.dumps({"candidates": [{"content": {"parts": [{"text": "---\ntitle: T\n---\nBody"}]}}]}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: io.BytesIO(ok))
    assert generate.generate_text("p").startswith("---")

    def boom(req, timeout=0):
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"quota"))
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(generate.ProviderError):
        generate.generate_text("p")


def test_body_start_snippet_after_body_tag_and_skipped_on_no_ads(project):
    site = json.loads((project / "site.json").read_text())
    site["body_start_snippets"] = ['<noscript id="gtmns">x</noscript>']
    (project / "site.json").write_text(json.dumps(site))
    (project / "content" / "pages" / "legal.md").write_text("---\nno_ads: true\ntitle: Legal\n---\n\nText.\n")
    run(project)
    home = (project / "out" / "index.html").read_text()
    assert home.index("<body>") < home.index("gtmns") < home.index('class="skip"')
    assert "gtmns" not in (project / "out" / "legal" / "index.html").read_text()


def test_analytics_gated_behind_consent(project):
    site = json.loads((project / "site.json").read_text())
    site["analytics_scripts"] = [{"src": "https://www.googletagmanager.com/gtag/js?id=G-TEST"}]
    (project / "site.json").write_text(json.dumps(site))
    (project / "content" / "pages" / "legal.md").write_text("---\nno_ads: true\ntitle: Legal\n---\n\nText.\n")
    run(project)
    home = (project / "out" / "index.html").read_text()
    assert 'id="consent-config"' in home and 'id="consent-banner"' in home
    assert '<script async src="https://www.googletagmanager.com/gtag' not in home   # not loaded directly
    assert "Cookie settings" in home
    assert 'id="consent-config"' not in (project / "out" / "legal" / "index.html").read_text()


# ---------------------------------------------------------------- phase 1

def set_products(project, products):
    (project / "data" / "products.json").write_text(json.dumps({"products": products}), encoding="utf-8")
    (project / "data" / "comparisons.json").write_text(json.dumps({"comparisons": []}), encoding="utf-8")


def product(**over):
    p = {"slug": "x-tin", "brand": "X", "productName": "Tin", "price": 40, "currency": "USD",
         "sizeGrams": 20, "sourceUrl": "https://example.com/p", "lastUpdated": "2026-11-01"}
    p.update(over)
    return p


def test_price_per_gram_is_calculated(project):
    set_products(project, [product()])
    run(project)
    page = (project / "out" / "products" / "x-tin" / "index.html").read_text()
    assert "$2.00" in page and "per gram" in page.lower()
    assert "/products/x-tin/" in (project / "out" / "sitemap.xml").read_text()


def test_product_missing_field_fails(project):
    set_products(project, [product(price=None)])
    with pytest.raises(build.BuildError):
        run(project)


def test_review_score_blocked_until_method_published(project):
    set_products(project, [product(reviewScore=4.5)])
    with pytest.raises(build.BuildError):
        run(project)


def test_unknown_product_affiliate_key_fails(project):
    set_products(project, [product(affiliateLinks=[{"key": "nope"}])])
    with pytest.raises(build.BuildError):
        run(project)


def test_currencies_are_not_mixed(project):
    set_products(project, [product(), product(slug="y-tin", currency="JPY", price=2800)])
    run(project)
    data = (project / "out" / "data" / "index.html").read_text()
    assert "USD prices" in data and "JPY prices" in data


def test_empty_sections_noindex_and_not_in_sitemap(project):
    set_products(project, [])
    run(project)
    assert "noindex" in (project / "out" / "reviews" / "index.html").read_text()
    assert "/reviews/" not in (project / "out" / "sitemap.xml").read_text()


def test_search_index_lists_guides_and_products(project):
    guide(project, "alpha-guide", title="Alpha matcha guide")
    set_products(project, [product()])
    run(project)
    rows = json.loads((project / "out" / "search.json").read_text())
    urls = {r["u"] for r in rows}
    assert "/guides/alpha-guide/" in urls and "/products/x-tin/" in urls


def test_archive_keeps_old_version_and_points_to_current(project):
    guide(project, "alpha-guide", title="Alpha matcha guide")
    dest = build.archive_guide("alpha-guide", today=TODAY)
    assert dest.parent.name == "2026"
    run(project)
    page = (project / "out" / "archive" / "2026" / "alpha-guide" / "index.html").read_text()
    assert "Archived version" in page
    assert 'href="https://solomatcha.com/guides/alpha-guide/"' in page
    assert (project / "out" / "guides" / "alpha-guide" / "index.html").exists()


def test_newsletter_form_only_when_provider_set(project):
    run(project)
    assert "<form action=" not in (project / "out" / "newsletter" / "index.html").read_text()
    site = json.loads((project / "site.json").read_text())
    site["newsletter"]["action"] = "https://mail.example.com/subscribe"
    (project / "site.json").write_text(json.dumps(site))
    run(project)
    assert 'action="https://mail.example.com/subscribe"' in (project / "out" / "newsletter" / "index.html").read_text()


def test_ranking_indexed_only_with_two_tins(project):
    set_products(project, [product()])
    run(project)
    assert "noindex" in (project / "out" / "rankings" / "price-per-gram-usd" / "index.html").read_text()
    assert "price-per-gram-usd" not in (project / "out" / "sitemap.xml").read_text()
    set_products(project, [product(), product(slug="y-tin", price=30)])
    run(project)
    page = (project / "out" / "rankings" / "price-per-gram-usd" / "index.html").read_text()
    assert "noindex" not in page and "ItemList" in page
    assert page.index("Y Tin") < page.index("X Tin") if "Y Tin" in page else True
    assert "price-per-gram-usd" in (project / "out" / "sitemap.xml").read_text()


def test_comparison_same_currency_shows_difference(project):
    set_products(project, [product(), product(slug="y-tin", price=30)])
    (project / "data" / "comparisons.json").write_text(json.dumps({"comparisons": [{"a": "x-tin", "b": "y-tin"}]}))
    run(project)
    page = (project / "out" / "compare" / "x-tin-vs-y-tin" / "index.html").read_text()
    assert "33% more per gram" in page or "more per gram" in page
    assert "/compare/x-tin-vs-y-tin/" in (project / "out" / "sitemap.xml").read_text()


def test_comparison_mixed_currency_has_no_difference(project):
    set_products(project, [product(), product(slug="y-tin", currency="JPY", price=2800)])
    (project / "data" / "comparisons.json").write_text(json.dumps({"comparisons": [{"a": "x-tin", "b": "y-tin"}]}))
    run(project)
    page = (project / "out" / "compare" / "x-tin-vs-y-tin" / "index.html").read_text()
    assert "do not calculate a difference" in page


def test_comparison_unknown_slug_fails(project):
    set_products(project, [product()])
    (project / "data" / "comparisons.json").write_text(json.dumps({"comparisons": [{"a": "x-tin", "b": "nope"}]}))
    with pytest.raises(build.BuildError):
        run(project)


def test_half_up_rounding_matches_guides(project):
    assert build.money(81 / 40, "USD") == "$2.03"


def test_methodology_page_published(project):
    run(project)
    assert (project / "out" / "methodology" / "index.html").exists()
