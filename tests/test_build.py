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
