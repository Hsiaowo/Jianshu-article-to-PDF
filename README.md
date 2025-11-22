# JianShu Article to PDF

Small Python script that crawls a JianShu article (including inline images) and exports it as a tidy PDF.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python3 crawler.py "https://www.jianshu.com/p/1003a129be45" \
  --output article.pdf \
  --font "/Library/Fonts/Arial Unicode.ttf"
```

- `url` (positional): full JianShu article URL to crawl.
- `--output`: where to save the PDF (default `article.pdf`).
- `--font`: path to a TTF font that supports the article language. On macOS, `/Library/Fonts/Arial Unicode.ttf` is auto-detected; supply your own on other systems for Chinese text.

## How it works

1. Fetches the page and extracts the `__NEXT_DATA__` JSON payload JianShu embeds.
2. Pulls the article HTML from `free_content`, walks headings/paragraphs/lists/code blocks/images in order.
3. Downloads inline images, then renders everything into a PDF with page numbers and basic spacing.

## Notes

- Paid/locked content is not handled; the script only captures the `free_content` portion.
- If you see garbled non-ASCII text, rerun with a Unicode-capable TTF via `--font`.
