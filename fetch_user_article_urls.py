import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

USER_PAGE = "https://www.jianshu.com/u/a8f8f391aa46"
PREFIX = "https://www.jianshu.com/p/"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


def get_article_urls():
    all_urls = set()
    max_pages = 50  # safety limit

    for page in range(1, max_pages + 1):
        print(f"Fetching page {page}...")
        params = {"page": page}
        resp = requests.get(USER_PAGE, params=params, headers=headers, timeout=10)

        # Stop if the page doesn’t exist or blocked
        if resp.status_code != 200:
            print(f"Stop at page {page}, status code = {resp.status_code}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        page_urls_before = len(all_urls)

        # Find all <a> tags with href including /p/
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/p/") or href.startswith(PREFIX):
                full_url = urljoin("https://www.jianshu.com", href)
                if full_url.startswith(PREFIX):
                    all_urls.add(full_url)

        # If this page didn’t add any new URLs, assume we reached the end
        if len(all_urls) == page_urls_before:
            print("No new URLs on this page, stopping.")
            break

    return sorted(all_urls)


if __name__ == "__main__":
    urls = get_article_urls()
    print("\nFound URLs:")
    for u in urls:
        print(u)
    print(f"\nTotal: {len(urls)}")
