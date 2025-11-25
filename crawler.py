#!/usr/bin/env python3
"""
JianShu crawler that downloads an article (including inline images)
and exports it to a neatly formatted PDF.
"""
import argparse
import json
import mimetypes
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from fpdf import FPDF


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
DEFAULT_FONT_CANDIDATES = [
    Path("/Library/Fonts/Arial Unicode.ttf"),
]
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "Output"
DEFAULT_USER_PAGE = "https://www.jianshu.com/u/a8f8f391aa46"


class ArticlePDF(FPDF):
    """Simple PDF renderer with a footer and basic spacing."""

    def __init__(self, base_font: str) -> None:
        super().__init__()
        self.base_font = base_font

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font(self.base_font, size=9)
        self.set_text_color(130, 130, 130)
        self.cell(0, 10, f"Page {self.page_no()}", align="R")


def _normalize_img_src(src: str, page_url: str) -> str:
    src = src.strip()
    if src.startswith("//"):
        return f"https:{src}"
    if src.startswith("/"):
        return urljoin(page_url, src)
    return src


def _pick_font_path(cli_font: Optional[str]) -> Optional[Path]:
    if cli_font:
        path = Path(cli_font).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Font file not found: {path}")
        return path
    for candidate in DEFAULT_FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _contains_non_ascii(text: str) -> bool:
    return any(ord(ch) > 127 for ch in text)


def fetch_user_article_urls(user_page: str, max_pages: int = 50) -> List[str]:
    all_urls = set()
    prefix = "https://www.jianshu.com/p/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    for page in range(1, max_pages + 1):
        params = {"page": page}
        resp = requests.get(user_page, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        before = len(all_urls)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/p/") or href.startswith(prefix):
                full_url = urljoin("https://www.jianshu.com", href).split("#", 1)[0]
                if full_url.startswith(prefix):
                    all_urls.add(full_url)

        if len(all_urls) == before:
            break

    return sorted(all_urls)


def fetch_article_html(url: str, session: requests.Session) -> str:
    resp = session.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.jianshu.com/",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.text


def extract_note_payload(page_html: str) -> Dict:
    soup = BeautifulSoup(page_html, "html.parser")
    data_script = soup.find("script", id="__NEXT_DATA__")
    if not data_script or not data_script.string:
        raise ValueError("Unable to locate __NEXT_DATA__ payload on the page.")
    payload = json.loads(data_script.string)
    note_state = payload.get("props", {}).get("initialState", {}).get("note", {})
    status = note_state.get("status")
    if status != "success":
        raise ValueError(f"Failed to load article content (status={status}).")
    return note_state.get("data", {})


def _strip_images(node: Tag) -> None:
    for img in node.find_all("img"):
        img.decompose()


def parse_content_elements(content_html: str, page_url: str) -> List[Dict]:
    soup = BeautifulSoup(content_html, "html.parser")
    allowed_tags = ("h1", "h2", "h3", "h4", "p", "blockquote", "pre", "img", "ul", "ol", "hr")
    elements: List[Dict] = []
    for node in soup.find_all(allowed_tags):
        if isinstance(node, NavigableString):
            continue
        if node.name == "img":
            src_attr = node.get("data-original-src") or node.get("data-src") or node.get("src")
            if src_attr:
                elements.append({"type": "image", "src": _normalize_img_src(src_attr, page_url)})
            continue
        if node.name in ("ul", "ol"):
            items = [li.get_text(" ", strip=True) for li in node.find_all("li", recursive=False)]
            if items:
                elements.append({"type": "list", "ordered": node.name == "ol", "items": items})
            continue
        text = node.get_text(" ", strip=True)
        if node.name == "hr":
            elements.append({"type": "divider"})
            continue
        if not text:
            continue
        if node.name == "blockquote":
            elements.append({"type": "quote", "text": text})
        elif node.name == "pre":
            code_text = node.get_text("\n", strip=True)
            elements.append({"type": "code", "text": code_text})
        elif node.name.startswith("h"):
            level = int(node.name[1]) if node.name[1].isdigit() else 4
            elements.append({"type": "heading", "level": level, "text": text})
        else:
            _strip_images(node)
            html = node.decode_contents().strip()
            elements.append({"type": "paragraph_html", "html": html})
    return elements


def download_images(elements: Iterable[Dict], session: requests.Session, tmpdir: Path) -> None:
    for idx, element in enumerate(elements):
        if element.get("type") != "image":
            continue
        url = element["src"]
        try:
            resp = session.get(url, stream=True, timeout=20, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
        except Exception as exc:
            print(f"[warn] Failed to download image {url}: {exc}", file=sys.stderr)
            continue

        content_type = resp.headers.get("Content-Type", "")
        ext = mimetypes.guess_extension(content_type.split(";")[0]) or Path(urlparse(url).path).suffix
        ext = ext or ".jpg"
        img_path = tmpdir / f"img_{idx}{ext}"
        with open(img_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        element["path"] = img_path


def render_pdf(
    title: str,
    author: Optional[str],
    elements: List[Dict],
    output: Path,
    font_path: Optional[Path],
) -> None:
    base_font = "Helvetica"
    custom_font_loaded = False
    pdf = ArticlePDF(base_font)
    pdf.set_auto_page_break(auto=True, margin=15)

    if font_path:
        base_font = font_path.stem
        pdf.add_font(base_font, fname=str(font_path))
        custom_font_loaded = True
        pdf.base_font = base_font

    pdf.add_page()
    heading_style = "" if custom_font_loaded else "B"

    pdf.set_title(title)
    if author:
        pdf.set_author(author)

    pdf.set_font(base_font, style=heading_style, size=20)
    pdf.multi_cell(0, 12, title)
    pdf.ln(4)

    if author:
        pdf.set_font(base_font, size=11)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 8, f"作者: {author}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

    epw = pdf.w - pdf.l_margin - pdf.r_margin
    for element in elements:
        etype = element.get("type")
        if etype == "heading":
            level = element.get("level", 3)
            size = max(14, 20 - level * 2)
            pdf.set_font(base_font, style=heading_style, size=size)
            pdf.multi_cell(0, 8, element["text"])
            pdf.ln(1)
        elif etype == "paragraph_html":
            pdf.set_font(base_font, size=12)
            # Wrap with <p> to leverage fpdf2's HTML renderer for inline <b>/<i> etc.
            pdf.write_html(f"<p>{element['html']}</p>")
            pdf.ln(1)
        elif etype == "quote":
            pdf.set_font(base_font, size=11)
            pdf.set_fill_color(245, 245, 245)
            pdf.set_text_color(60, 60, 60)
            pdf.multi_cell(0, 7, element["text"], fill=True)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
        elif etype == "code":
            pdf.set_font(base_font, size=10)
            pdf.set_fill_color(250, 248, 245)
            pdf.multi_cell(0, 6, element["text"], fill=True)
            pdf.ln(1)
        elif etype == "list":
            pdf.set_font(base_font, size=12)
            for i, item in enumerate(element["items"], 1):
                bullet = f"{i}. " if element.get("ordered") else "• "
                pdf.multi_cell(0, 7, f"{bullet}{item}")
            pdf.ln(1)
        elif etype == "divider":
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + epw, pdf.get_y())
            pdf.ln(3)
        elif etype == "image":
            img_path = element.get("path")
            if not img_path:
                continue
            img_w = min(epw, 170)
            pdf.image(str(img_path), w=img_w)
            pdf.ln(3)

    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output))


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "", name).strip()
    return cleaned or "article"


def _rename_output_with_title(output: Path, title: str) -> Path:
    """Return a path that uses the article title as filename, avoiding collisions."""
    sanitized = _sanitize_filename(title)
    suffix = output.suffix or ".pdf"
    candidate = output.with_name(f"{sanitized}{suffix}")
    if candidate == output:
        return output

    counter = 1
    while candidate.exists():
        candidate = output.with_name(f"{sanitized}_{counter}{suffix}")
        counter += 1
    if candidate.parent != output.parent:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _ensure_title_filename(output: Path, title: str) -> Path:
    """
    Rename an existing file to the title-based filename when needed.
    If the target name already exists, keep the existing one and leave the original untouched.
    """
    target = _rename_output_with_title(output, title)
    if target == output:
        return output

    if target.exists():
        # File is already stored with the title; keep as-is.
        return target

    if output.exists():
        output.rename(target)
        return target

    return target


def crawl(
    url: str,
    output: Path,
    font_path: Optional[Path],
    session: Optional[requests.Session] = None,
    note_data: Optional[Dict] = None,
) -> Path:
    session = session or requests.Session()
    if note_data is None:
        page_html = fetch_article_html(url, session)
        note_data = extract_note_payload(page_html)

    title = note_data.get("public_title") or "JianShu Article"
    author = note_data.get("user", {}).get("nickname")
    content_html = note_data.get("free_content", "")
    elements = parse_content_elements(content_html, url)

    output = _rename_output_with_title(output, title)

    if not font_path and any(_contains_non_ascii(el.get("text", "")) for el in elements):
        print("[warn] Content includes non-ASCII characters; specify --font for correct rendering.")

    with tempfile.TemporaryDirectory() as tmpdir:
        download_images(elements, session, Path(tmpdir))
        output.parent.mkdir(parents=True, exist_ok=True)
        render_pdf(title, author, elements, output, font_path)

    print(f"[ok] Saved PDF to {output}")
    return output


def _read_stdin_urls() -> List[str]:
    urls: List[str] = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        urls.extend(line.split())
    return urls


def _slug_from_url(url: str) -> str:
    return _sanitize_filename(Path(urlparse(url).path).name or "article")


def _output_path_for_url(url: str, output_dir: Path, output_override: Optional[Path]) -> Path:
    if output_override:
        return output_override
    slug = _slug_from_url(url)
    return output_dir / f"{slug}.pdf"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download JianShu articles to PDF.")
    parser.add_argument(
        "urls",
        nargs="*",
        help="Optional whitespace-separated JianShu article URLs (skipped when --user-page is used).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output PDF path (only valid when a single URL is provided).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for saving PDFs when multiple URLs are provided (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--font",
        help="Path to a TTF font that supports the article language (defaults to Arial Unicode on macOS if present).",
    )
    parser.add_argument(
        "--user-page",
        default=DEFAULT_USER_PAGE,
        help="JianShu user page to crawl for article URLs "
        f"(default: {DEFAULT_USER_PAGE}). If provided, URLs from this page are used and any positional URLs are ignored.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="Max pagination pages to scan when collecting URLs from the user page.",
    )
    args = parser.parse_args()

    urls: List[str] = []
    if args.user_page:
        urls = fetch_user_article_urls(args.user_page, max_pages=args.max_pages)
    elif args.urls:
        urls = list(args.urls)
    elif not sys.stdin.isatty():
        urls = _read_stdin_urls()

    if not urls:
        parser.error("No JianShu article URLs found from --user-page, stdin, or arguments.")

    if args.output and len(urls) != 1:
        parser.error("--output can only be used when downloading a single URL.")

    font_path = _pick_font_path(args.font)
    if args.font and not font_path:
        parser.error(f"Font file not found: {args.font}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    failed: List[str] = []
    total = len(urls)
    session = requests.Session()
    for idx, url in enumerate(urls, 1):
        base_output = _output_path_for_url(url, output_dir, Path(args.output) if args.output else None)
        note_data: Optional[Dict] = None
        title_for_naming: Optional[str] = None
        try:
            page_html = fetch_article_html(url, session)
            note_data = extract_note_payload(page_html)
            title_for_naming = note_data.get("public_title") or "JianShu Article"
        except Exception as exc:
            print(f"[warn] Could not prefetch metadata for {url}: {exc}", file=sys.stderr)

        if title_for_naming:
            target_output = _rename_output_with_title(base_output, title_for_naming)
            if target_output.exists():
                print(f"[{idx}/{total}] Skip (titled exists) {target_output.name}")
                continue
            if base_output.exists():
                renamed = _ensure_title_filename(base_output, title_for_naming)
                print(f"[{idx}/{total}] Renamed to {renamed.name}")
                continue
        elif base_output.exists():
            print(f"[{idx}/{total}] Skip (exists) {base_output.name}")
            continue

        print(f"[{idx}/{total}] Downloading {url}")
        try:
            crawl(url, base_output, font_path, session=session, note_data=note_data)
        except Exception as exc:
            print(f"[warn] Skipping {url}: {exc}", file=sys.stderr)
            failed.append(url)
            continue

    if failed:
        fail_log = output_dir / "failed_urls.txt"
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(fail_log, "w", encoding="utf-8") as f:
            for url in failed:
                f.write(f"{url}\n")
        print(f"[info] Recorded {len(failed)} failed URL(s) to {fail_log}")


if __name__ == "__main__":
    main()
