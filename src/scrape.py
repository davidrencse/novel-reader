"""
scrape.py — pull a chapter's text from witchculttranslation.com.

The content lives in:  <article> ... <div class="entry-content"> <p>...</p> ...

Output is a plain-text file: first line = title, blank line, then one
paragraph per line. This is for PERSONAL, LOCAL playback only — the text is
a fan translation of copyrighted work; don't redistribute it.
"""
from __future__ import annotations

import re
import sys
import argparse
import urllib.request
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    print("Missing dependency: beautifulsoup4. Run setup.ps1 first.", file=sys.stderr)
    raise

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) rezero-reader/1.0"}

# Paragraphs matching any of these are navigation / credits / support blurbs,
# not story prose, and get dropped.
BOILERPLATE = re.compile(
    r"(patreon|ko-?fi|paypal|donat|previous chapter|next chapter|"
    r"table of contents|translat(ed|or|ion)|proofread|edited by|editor:|"
    r"support (us|the)|join (our|the) discord|copyright|all rights reserved)",
    re.I,
)


def fetch_html(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    # Site is UTF-8; be tolerant of stray bytes.
    return raw.decode("utf-8", "ignore")


def parse_chapter(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")

    # Title: prefer the WP entry-title, fall back to <title>.
    title = ""
    h = soup.find(class_="entry-title")
    if h and h.get_text(strip=True):
        title = h.get_text(strip=True)
    elif soup.title:
        title = soup.title.get_text(strip=True)
    title = re.sub(r"\s*[|–-]\s*Witch Cult Translations.*$", "", title).strip()

    content = soup.find("div", class_="entry-content")
    if content is None:
        raise RuntimeError("Could not find <div class='entry-content'> — site layout may have changed.")

    paragraphs: list[str] = []
    for p in content.find_all("p"):
        # Drop paragraphs that are purely a link (nav) or empty.
        text = p.get_text(" ", strip=True)
        if not text:
            continue
        if BOILERPLATE.search(text):
            continue
        # A <p> whose text is entirely inside a single <a> is navigation.
        a = p.find("a")
        if a and a.get_text(strip=True) == text:
            continue
        text = re.sub(r"\s+", " ", text).strip()
        paragraphs.append(text)

    # Strip decorative symbols and drop scene-break divider lines.
    from .textutil import clean_paragraphs
    paragraphs = clean_paragraphs(paragraphs)

    if not paragraphs:
        raise RuntimeError("Found the content container but no story paragraphs — check the URL.")
    return title or "Untitled Chapter", paragraphs


def _abs(base: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, href)


def parse_full(html: str, base_url: str = "") -> dict:
    """Rich parse: title, paragraphs, images, links, prev/next chapter titles."""
    soup = BeautifulSoup(html, "html.parser")
    title, paragraphs = parse_chapter(html)
    content = soup.find("div", class_="entry-content")

    # Featured image (og:image) + any illustrations inside the article body.
    images: list[str] = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        images.append(og["content"])
    article = soup.find("article") or content
    for img in (article.find_all("img") if article else []):
        src = img.get("src") or (img.get("data-src") or "")
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[0].strip().split(" ")[0]
        if not src:
            continue
        src = _abs(base_url, src)
        low = src.lower()
        if any(k in low for k in ("gravatar", "emoji", "avatar", "icon", "logo", "smiley")):
            continue
        if src not in images:
            images.append(src)

    # In-body hyperlinks (skip pure navigation / boilerplate anchors).
    links: list[dict] = []
    seen = set()
    for a in (content.find_all("a") if content else []):
        href = a.get("href") or ""
        text = a.get_text(" ", strip=True)
        if not href or not text or href.startswith("#"):
            continue
        if BOILERPLATE.search(text):
            continue
        href = _abs(base_url, href)
        if href in seen:
            continue
        seen.add(href)
        links.append({"text": text[:80], "href": href})

    # Previous / next chapter navigation (WordPress rel links + nav anchors).
    nav = {"prev": None, "next": None}
    for rel, key in (("prev", "prev"), ("next", "next")):
        l = soup.find("link", rel=rel)
        a = soup.select_one(f".nav-{'previous' if key=='prev' else 'next'} a") \
            or soup.select_one(f"a[rel~={rel}]")
        nav[key] = {
            "title": (a.get_text(" ", strip=True) if a else "") or None,
            "href": _abs(base_url, (a.get("href") if a else "") or (l.get("href") if l else "")) or None,
        }
        if not nav[key]["title"] and not nav[key]["href"]:
            nav[key] = None

    # Category / arc chips.
    cats = [c.get_text(strip=True) for c in soup.select("a[rel~=category], .cat-links a")]
    cats = list(dict.fromkeys([c for c in cats if c]))[:6]

    # Post ID (for the async comments REST call): hidden input or article class.
    post_id = None
    inp = soup.select_one("#comment_post_ID")
    if inp and inp.get("value"):
        post_id = inp["value"]
    if not post_id:
        art = soup.find("article")
        m = re.search(r"post-(\d+)", " ".join(art.get("class") or [])) if art else None
        if m:
            post_id = m.group(1)

    return {
        "title": title,
        "paragraphs": paragraphs,
        "images": images[:12],
        "links": links[:40],
        "nav": nav,
        "categories": cats,
        "post_id": post_id,
    }


def fetch_comments(page_url: str, post_id: str, limit: int = 15, timeout: int = 15) -> list[dict]:
    """Fetch reader comments via the WordPress REST API (they load async, not in HTML).

    Returns newest-first [{author, content, date, link}]. Never raises — comments
    are a nice-to-have, so any failure yields an empty list.
    """
    import json
    from urllib.parse import urlsplit

    if not post_id:
        return []
    parts = urlsplit(page_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    api = (f"{origin}/wp-json/wp/v2/comments?post={post_id}"
           f"&per_page={limit}&order=desc&orderby=date")
    try:
        req = urllib.request.Request(api, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return []

    out: list[dict] = []
    for c in data if isinstance(data, list) else []:
        rendered = (c.get("content") or {}).get("rendered", "")
        text = BeautifulSoup(rendered, "html.parser").get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        author = (c.get("author_name") or "").strip() or "Reader"
        out.append({
            "author": author[:60],
            "content": text[:600],
            "date": (c.get("date") or "")[:10],
            "link": c.get("link") or "",
            "is_reply": bool(c.get("parent")),
        })
    return out


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:80] or "chapter"


def save(title: str, paragraphs: list[str], out_dir: Path, name: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = (name or slugify(title)) + ".txt"
    path = out_dir / fname
    path.write_text(title + "\n\n" + "\n".join(paragraphs), encoding="utf-8")
    return path


def scrape_to_file(url: str, out_dir: Path, name: str | None = None) -> tuple[Path, str, list[str]]:
    html = fetch_html(url)
    title, paragraphs = parse_chapter(html)
    path = save(title, paragraphs, out_dir, name)
    return path, title, paragraphs


def load_text_file(path: Path) -> tuple[str, list[str]]:
    """Read a saved/pasted chapter file: line 1 = title, rest = paragraphs."""
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [ln.strip() for ln in lines]
    # Title = first non-empty line; paragraphs = remaining non-empty lines.
    title = ""
    body: list[str] = []
    for ln in lines:
        if not ln:
            continue
        if not title:
            title = ln
        else:
            body.append(ln)
    from .textutil import clean_paragraphs
    return title or "Untitled Chapter", clean_paragraphs(body)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scrape a Re:Zero chapter to a text file.")
    ap.add_argument("url")
    ap.add_argument("--out", default="data/chapters")
    ap.add_argument("--name", default=None, help="output filename (without .txt)")
    args = ap.parse_args()
    path, title, paras = scrape_to_file(args.url, Path(args.out), args.name)
    print(f"Saved: {path}")
    print(f"Title: {title}")
    print(f"Paragraphs: {len(paras)}")
