"""
fetch_tanishq_gold_rate.py

Purpose:
    Scrape today's 22 Karat gold rate (for 1 gram) from Tanishq's public gold
    rate webpage, and save it as a small JSON file (fetch_tanishq_gold_rate.json)
    so the rate can be tracked over time.

How it works, in plain English:
    1. Download the HTML of Tanishq's gold rate page, pretending to be a
       normal web browser (some sites reject requests that don't look like
       they come from a browser).
    2. Parse that HTML with BeautifulSoup, a library that turns raw HTML
       text into something we can search through (find tables, cells, etc.).
    3. Find the "22 Kt Gold Rate" table, then the row for "1 G" (1 gram),
       then the "Today" price in that row.
    4. Clean up the price text (remove "₹", commas, extra spaces) and turn
       it into a plain number.
    5. Save that number, along with today's date, to a JSON file.

IMPORTANT NOTE ON FRAGILITY:
    This script reads a public webpage, not an official API. Tanishq could
    redesign this page at any time, which would change the HTML structure
    and break the scraper. If that happens, you (or a developer) will need
    to look at the page's HTML again and update the selectors below
    (see find_22kt_table() and extract_today_price_for_1g()).

NOTE ON CLOUDFLARE:
    Tanishq's site sits behind Cloudflare, which shows visitors an
    automated JavaScript "challenge" page if they don't look like a real
    browser - this blocks Python's plain `requests` library with an HTTP
    403 error, even with browser-like headers set. `cloudscraper` is a thin
    wrapper around `requests` that knows how to solve Cloudflare's basic JS
    challenge, so it's used here instead of `requests` directly. If
    Cloudflare changes/strengthens its challenge in the future, this may
    stop working and `cloudscraper` would need to be updated (or a
    headless-browser tool like Playwright/Selenium used instead).

Run this manually with:  python fetch_tanishq_gold_rate.py
"""

import os
import re
import sys
import json
from datetime import datetime

import requests
import cloudscraper
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# STEP 0: CONFIGURATION
# ---------------------------------------------------------------------------

SOURCE_URL = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN"

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_tanishq_gold_rate.json")

# A realistic browser User-Agent. Some websites block requests that don't
# have one, since it's a common sign of an automated bot rather than a
# normal visitor using a browser like Chrome.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT_SECONDS = 20


def fetch_page_html(url: str) -> str:
    """
    STEP 1: Download the raw HTML of the gold rate page.

    We use `cloudscraper.create_scraper()` instead of a plain `requests`
    session because Tanishq's site is behind Cloudflare's bot-check (see
    the "NOTE ON CLOUDFLARE" comment at the top of this file). The
    `browser=` setting tells cloudscraper what kind of browser fingerprint
    to imitate while solving the challenge.

    Raises a RuntimeError with a clear message if the request fails.
    """
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    response = scraper.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)

    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to fetch the Tanishq gold rate page.\n"
            f"URL: {url}\n"
            f"HTTP status: {response.status_code}\n"
            "The site may be temporarily down, or its Cloudflare bot-check "
            "may have gotten stricter (which could mean cloudscraper needs "
            "to be updated: `pip install --upgrade cloudscraper`)."
        )

    return response.text


def find_22kt_table(soup: BeautifulSoup):
    """
    STEP 2 (part A): Locate the "22 Kt Gold Rate" table in the parsed HTML.

    Tanishq's page currently marks this table with the CSS class
    "goldrate-table-22kt", which is the most direct way to find it. As a
    backup (in case that class name ever changes), we also try finding the
    "22 Kt Gold Rate" heading text and taking the table that follows it.
    """
    table = soup.find("table", class_="goldrate-table-22kt")
    if table:
        return table

    # Fallback: search headings (h1-h4) for the visible label text, then
    # grab the next table that appears after it in the page.
    heading = soup.find(
        lambda tag: tag.name in ("h1", "h2", "h3", "h4")
        and "22 kt gold rate" in tag.get_text(strip=True).lower()
    )
    if heading:
        return heading.find_next("table")

    return None


def extract_price_number(cell_text: str):
    """
    Given a table cell's raw text (e.g. "₹ 13195 0 (0.00%)"), pull out just
    the first rupee amount and convert it to a plain number.

    The "Today" cell's text looks like:  "₹\n13195\n\n0\n(0.00%)"
    (the "0 (0.00%)" part is the change-vs-yesterday indicator, nested
    inside the same cell). We only want the number right after the ₹ sign.
    """
    match = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", cell_text)
    if not match:
        return None

    number_text = match.group(1).replace(",", "")
    # Use a float if there's a decimal point, otherwise a plain integer.
    return float(number_text) if "." in number_text else int(number_text)


def extract_today_price_for_1g(table) -> float:
    """
    STEP 2 (part B) + STEP 3: Within the 22kt table, find the row whose
    first column is "1 G", then pull the numeric price out of its "Today"
    column (the second <td> in that row).
    """
    body_rows = table.find_all("tr")

    for row in body_rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue  # skip header rows or malformed rows

        grammage_label = cells[0].get_text(strip=True)
        if grammage_label.replace(" ", "").upper() != "1G":
            continue

        today_cell_text = cells[1].get_text(" ", strip=True)
        price = extract_price_number(today_cell_text)

        if price is None:
            raise RuntimeError(
                "Found the '1 G' row, but couldn't find a rupee amount in "
                f"its 'Today' cell. Raw cell text was: {today_cell_text!r}"
            )

        return price

    raise RuntimeError("Could not find a '1 G' row inside the 22 Kt Gold Rate table.")


def save_result(data: dict, path: str) -> None:
    """
    STEP 6: Write the result to a JSON file, pretty-printed.
    This is only ever called after extraction has fully succeeded, so a
    failed scrape never overwrites a previously saved good file.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    try:
        # -----------------------------------------------------------------
        # STEP 1: Fetch the page HTML.
        # -----------------------------------------------------------------
        print(f"Fetching gold rate page: {SOURCE_URL}")
        html = fetch_page_html(SOURCE_URL)

        # -----------------------------------------------------------------
        # STEP 2: Parse the HTML with BeautifulSoup.
        # -----------------------------------------------------------------
        soup = BeautifulSoup(html, "html.parser")

        # -----------------------------------------------------------------
        # STEP 3: Find the 22 Kt Gold Rate table.
        # -----------------------------------------------------------------
        table = find_22kt_table(soup)
        if table is None:
            raise RuntimeError(
                "Could not find the '22 Kt Gold Rate' table on the page. "
                "Tanishq may have redesigned this page - the script's "
                "selectors in find_22kt_table() will need updating."
            )

        # -----------------------------------------------------------------
        # STEP 4 + 5: Extract and clean the "1 G" / "Today" price.
        # -----------------------------------------------------------------
        rate_inr = extract_today_price_for_1g(table)

        # -----------------------------------------------------------------
        # STEP 6: Build the result and save it to JSON.
        # -----------------------------------------------------------------
        result = {
            "date_fetched": datetime.now().strftime("%Y-%m-%d"),
            "karat": "22K",
            "weight_grams": 1,
            "rate_inr": rate_inr,
        }
        save_result(result, OUTPUT_FILE)

        # -----------------------------------------------------------------
        # STEP 7: Confirm success.
        # -----------------------------------------------------------------
        # Note: we deliberately print "Rs." instead of the "₹" symbol here,
        # since some Windows terminals use a default codepage that can't
        # display it and would crash this print statement.
        print(
            f"SUCCESS: Today's 22K gold rate (1g) is Rs. {rate_inr}. "
            f"Saved to '{OUTPUT_FILE}'."
        )

    except RuntimeError as e:
        # Errors we raised ourselves above (page structure changed, price
        # not found, etc.) - print a clean message instead of a traceback,
        # and do NOT touch the existing JSON file.
        print(f"\nERROR: {e}")
        sys.exit(1)
    except cloudscraper.exceptions.CloudflareException as e:
        # cloudscraper couldn't get past Cloudflare's bot-check this time
        # (e.g. it now requires solving a CAPTCHA, which cloudscraper's
        # free tier can't do automatically).
        print(
            f"\nERROR: Could not get past Tanishq's Cloudflare bot-check: {e}\n"
            "Try again later, or run: pip install --upgrade cloudscraper"
        )
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        # Network-level problems (no internet, DNS failure, timeout, etc.)
        print(f"\nERROR: A network problem occurred while fetching the page: {e}")
        sys.exit(1)
    except Exception as e:
        # Catch-all safety net so the script never crashes with a raw,
        # confusing traceback.
        print(f"\nERROR: An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
