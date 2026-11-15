#!/usr/bin/env python3
"""SoloMatcha static site generator.

Reads content/ (guides, pages, topics.csv), site.json and affiliates.json,
renders templates/ with Jinja2 and writes a deployable site to public/.

Usage:
    python3 build.py                 # published guides only (what production runs)
    python3 build.py --drafts        # also render guides marked draft: true
    python3 build.py --out DIR       # write somewhere other than public/
    python3 build.py --today DATE    # pretend today is DATE (YYYY-MM-DD)

The build FAILS (exit 1) when a published guide has any of: a TODO( marker,
a health-claim phrase, an unknown aff: key, missing front matter, or an
unknown category. Drafts only produce warnings, so work in progress is safe.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import math
import re
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent


class BuildError(Exception):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("\n".join(problems))


# ---------------------------------------------------------------- loading

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_front_matter(text: str):
    """Split '---\\nyaml\\n---\\nbody' into (dict, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                raise ValueError("front matter must be a mapping")
            return meta, parts[2].lstrip("\n")
    return {}, text


def as_date(value, label):
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{label}: bad date {value!r} (use YYYY-MM-DD)")


AFF_RE = re.compile(r'<a href="aff:([A-Za-z0-9_-]+)"')


def render_markdown(body: str, affiliates: dict, problems: list, label: str):
    """Markdown -> HTML. Returns (html, affiliate_link_count)."""
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc", "smarty"],
        extension_configs={"toc": {"permalink": False}},
    )
    out = md.convert(body)
    count = 0

    def repl(match):
        nonlocal count
        key = match.group(1)
        entry = affiliates.get(key)
        if not entry or not entry.get("url"):
            problems.append(f"{label}: unknown affiliate key aff:{key} (add it to affiliates.json)")
            return '<a href="#"'
        count += 1
        url = html.escape(entry["url"], quote=True)
        return f'<a href="{url}" rel="sponsored nofollow noopener" target="_blank"'

    out = AFF_RE.sub(repl, out)
    out = out.replace("<table>", '<div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table>')
    out = out.replace("</table>", "</table></div>")
    return out, count


def word_count(body: str) -> int:
    return len(re.findall(r"\w+", body))


def load_guides(site, affiliates, today, include_drafts, problems, warnings):
    guides = []
    patterns = [re.compile(p, re.I) for p in site.get("health_claim_patterns", [])]
    categories = site["categories"]
    folder = ROOT / "content" / "guides"
    for path in sorted(folder.glob("*.md")):
        label = f"guides/{path.name}"
        try:
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        except Exception as exc:  # yaml error etc.
            problems.append(f"{label}: cannot parse front matter ({exc})")
            continue
        draft = bool(meta.get("draft", False))
        if draft and not include_drafts:
            continue
        local = warnings if draft else problems  # drafts only warn
        slug = str(meta.get("slug") or path.stem)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            problems.append(f"{label}: slug {slug!r} must be lowercase letters, digits and hyphens")
            continue
        for field in ("title", "description", "category", "date"):
            if not meta.get(field):
                local.append(f"{label}: missing front matter field '{field}'")
        category = meta.get("category")
        if category and category not in categories:
            local.append(f"{label}: unknown category {category!r} (see site.json)")
        try:
            date = as_date(meta.get("date"), label) or today
            updated = as_date(meta.get("updated"), label)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if date > today and not draft:
            continue  # scheduled for later
        if "TODO(" in body or "TODO(" in json.dumps(meta, default=str):
            local.append(f"{label}: contains a TODO( marker - resolve it before publishing")
        for pat in patterns:
            m = pat.search(body) or pat.search(str(meta.get("description", ""))) or pat.search(str(meta.get("title", "")))
            if m:
                local.append(f"{label}: health-claim phrase {m.group(0)!r} - not allowed on this site")
        if re.search(r"^# ", body, re.M):
            local.append(f"{label}: body has a level-1 heading; the title comes from front matter, start at ##")
        desc = str(meta.get("description", ""))
        if desc and not (50 <= len(desc) <= 175):
            warnings.append(f"{label}: description is {len(desc)} chars (aim for 50-175)")
        html_body, aff_count = render_markdown(body, affiliates, local, label)
        guides.append({
            "slug": slug,
            "url": f"/guides/{slug}/",
            "title": str(meta.get("title", slug)),
            "description": desc,
            "category": category or "explainers",
            "category_label": categories.get(category, category or ""),
            "date": date,
            "updated": updated,
            "draft": draft,
            "html": html_body,
            "affiliate_count": aff_count,
            "minutes": max(1, math.ceil(word_count(body) / 200)),
        })
    guides.sort(key=lambda g: (g["date"], g["slug"]), reverse=True)
    seen = {}
    for g in guides:
        if g["slug"] in seen:
            problems.append(f"duplicate guide slug {g['slug']!r}")
        seen[g["slug"]] = True
    return guides


def load_pages(affiliates, problems, include_drafts=False):
    pages = []
    for path in sorted((ROOT / "content" / "pages").glob("*.md")):
        label = f"pages/{path.name}"
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        if meta.get("draft") and not include_drafts:
            continue
        if not meta.get("title"):
            problems.append(f"{label}: missing title")
            continue
        if "TODO(" in body and not meta.get("draft"):
            problems.append(f"{label}: contains a TODO( marker")
        html_body, _ = render_markdown(body, affiliates, problems, label)
        pages.append({
            "slug": str(meta.get("slug") or path.stem),
            "title": meta["title"],
            "description": meta.get("description") or meta["title"],
            "html": html_body,
            "footer": bool(meta.get("footer", True)),
            "no_ads": bool(meta.get("no_ads", False)),
        })
    return pages


def load_topics():
    path = ROOT / "content" / "topics.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------- output

def jsonld_for(site, guide):
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": guide["title"],
        "description": guide["description"],
        "datePublished": guide["date"].isoformat(),
        "dateModified": (guide["updated"] or guide["date"]).isoformat(),
        "author": {"@type": "Organization", "name": site["name"], "url": site["url"]},
        "publisher": {"@type": "Organization", "name": site["name"], "url": site["url"]},
        "mainEntityOfPage": site["url"] + guide["url"],
    }
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def month_groups(items):
    groups, current = [], None
    for g in items:
        label = g["date"].strftime("%B %Y")
        if current is None or current["label"] != label:
            current = {"label": label, "entries": []}
            groups.append(current)
        current["entries"].append(g)
    return groups


def build(out_dir: Path = None, include_drafts: bool = False, today: dt.date = None):
    out_dir = Path(out_dir or ROOT / "public")
    today = today or dt.date.today()
    problems, warnings = [], []

    site = load_json(ROOT / "site.json")
    affiliates = {k: v for k, v in (load_json(ROOT / "affiliates.json", {}) or {}).items() if not k.startswith("_")}
    guides = load_guides(site, affiliates, today, include_drafts, problems, warnings)
    pages = load_pages(affiliates, problems, include_drafts)
    topics = load_topics()

    if problems:
        raise BuildError(problems)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    static = ROOT / "static"
    if static.exists():
        shutil.copytree(static, out_dir, dirs_exist_ok=True)

    css_path = static / "assets" / "site.css"
    css_version = hashlib.sha1(css_path.read_bytes()).hexdigest()[:8] if css_path.exists() else "0"

    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")),
                      autoescape=select_autoescape(["html"]))
    env.filters["datefmt"] = lambda d: f"{d.strftime('%B')} {d.day}, {d.year}"

    base_ctx = {
        "site": site,
        "css_version": css_version,
        "page_slugs": [p["slug"] for p in pages],
        "footer_pages": [p for p in pages if p["footer"]],
        "total_guides": len(guides),
        "noindex": False,
    }

    def write(rel: str, template: str, **ctx):
        data = {**base_ctx, **ctx}
        data.setdefault("canonical", site["url"] + "/" + rel.replace("index.html", "") if rel != "404.html" else site["url"] + "/")
        text = env.get_template(template).render(**data)
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    # Category counts (only categories that have guides)
    cat_counts = {}
    for g in guides:
        cat_counts[g["category"]] = cat_counts.get(g["category"], 0) + 1
    categories = [{"slug": s, "label": l, "count": cat_counts[s]}
                  for s, l in site["categories"].items() if s in cat_counts]

    # Planned topics that do not have a guide yet
    have = {g["slug"] for g in guides}
    upcoming = [t for t in topics if t.get("status") == "planned" and t["slug"] not in have][:6]

    # Home
    write("index.html", "home.html",
          page_title=f"{site['name']}: {site['tagline']}",
          page_description=site["description"],
          latest=guides[:3], upcoming=upcoming)

    # Archive + category pages
    write("guides/index.html", "archive.html",
          page_title=f"All guides | {site['name']}",
          page_description=f"Every {site['name']} guide on buying, grading and preparing pure matcha, newest first.",
          groups=month_groups(guides), categories=categories, current_category=None, total=len(guides))
    for c in categories:
        items = [g for g in guides if g["category"] == c["slug"]]
        write(f"guides/topic/{c['slug']}/index.html", "archive.html",
              page_title=f"{c['label']} | {site['name']}",
              page_description=f"{c['label']} guides from {site['name']}, newest first.",
              groups=month_groups(items), categories=categories, current_category=c, total=len(items))

    # Guides
    for i, g in enumerate(guides):
        newer = guides[i - 1] if i > 0 else None
        older = guides[i + 1] if i + 1 < len(guides) else None
        same = [x for x in guides if x is not g and x["category"] == g["category"]]
        rest = [x for x in guides if x is not g and x not in same]
        related = (same + rest)[:3]
        write(f"guides/{g['slug']}/index.html", "guide.html",
              page_title=f"{g['title']} | {site['name']}",
              page_description=g["description"], og_type="article",
              guide=g, older=older, newer=newer, related=related, jsonld=jsonld_for(site, g),
              noindex=g["draft"])

    # Static pages
    for p in pages:
        write(f"{p['slug']}/index.html", "page.html",
              page_title=f"{p['title']} | {site['name']}", page_description=p["description"], page=p, no_ads=p["no_ads"])

    # 404
    write("404.html", "404.html", page_title=f"Page not found | {site['name']}",
          page_description="Page not found.", noindex=True, canonical=site["url"] + "/")

    # sitemap (never lists drafts)
    live = [g for g in guides if not g["draft"]]
    urls = [(site["url"] + "/", today), (site["url"] + "/guides/", live[0]["date"] if live else today)]
    urls += [(f"{site['url']}/guides/topic/{c['slug']}/", today) for c in categories]
    urls += [(site["url"] + g["url"], g["updated"] or g["date"]) for g in live]
    urls += [(f"{site['url']}/{p['slug']}/", today) for p in pages]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, last in urls:
        sm.append(f"  <url><loc>{xml_escape(loc)}</loc><lastmod>{last.isoformat()}</lastmod></url>")
    sm.append("</urlset>")
    (out_dir / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")

    # RSS feed (latest 20 published)
    items = []
    for g in live[:20]:
        pub = dt.datetime(g["date"].year, g["date"].month, g["date"].day, 12, 0, tzinfo=dt.timezone.utc)
        link = xml_escape(site["url"] + g["url"])
        items.append(
            f"    <item><title>{xml_escape(g['title'])}</title><link>{link}</link>"
            f"<guid>{link}</guid><pubDate>{pub.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
            f"<description>{xml_escape(g['description'])}</description></item>")
    feed = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n'
            f"    <title>{xml_escape(site['name'])}</title><link>{xml_escape(site['url'])}/</link>\n"
            f"    <description>{xml_escape(site['description'])}</description>\n"
            f"    <language>{site['language']}</language>\n" + "\n".join(items) + "\n</channel></rss>\n")
    (out_dir / "feed.xml").write_text(feed, encoding="utf-8")

    return {"guides": len(guides), "drafts": sum(1 for g in guides if g["draft"]),
            "pages": len(pages), "warnings": warnings, "out": str(out_dir)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the SoloMatcha site")
    ap.add_argument("--drafts", action="store_true", help="include draft guides (marked noindex)")
    ap.add_argument("--out", default=None, help="output directory (default: public)")
    ap.add_argument("--today", default=None, help="override today's date, YYYY-MM-DD")
    args = ap.parse_args(argv)
    today = dt.date.fromisoformat(args.today) if args.today else None
    try:
        summary = build(args.out, args.drafts, today)
    except BuildError as exc:
        print("BUILD FAILED - fix these before publishing:", file=sys.stderr)
        for p in exc.problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    for w in summary["warnings"]:
        print(f"warning: {w}")
    print(f"Built {summary['guides']} guide(s) ({summary['drafts']} draft), "
          f"{summary['pages']} page(s) -> {summary['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
