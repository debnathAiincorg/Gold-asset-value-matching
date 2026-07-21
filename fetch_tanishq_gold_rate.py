"""
fetch_tanishq_gold_rate.py

Purpose:
    Scrape today's 22 Karat gold rate (for 1 gram) from Tanishq's public gold
    rate webpage, and save it as a small JSON file (fetch_tanishq_gold_rate.json)
    so the rate can be tracked over time.

How it works, in plain English:
    1. Launch a headless Chromium browser (via Playwright) disguised to look
       like a normal desktop Chrome visitor (see "NOTE ON STEALTH" below),
       navigate to Tanishq's gold rate page, and wait for Cloudflare's
       automated bot-check to clear before reading the fully-rendered HTML.
       If this fails with a timeout/navigation/parsing error, retry up to
       3 times with an 8-second pause in between, since a Cloudflare
       challenge or a slow page load is often transient.
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
    browser - this blocks Python's plain `requests` library (and even
    `cloudscraper`, which used to be used here) with an HTTP 403 error,
    especially from shared/cloud IPs like GitHub Actions' runners. A real
    headless browser (Playwright driving Chromium) actually executes the
    challenge's JavaScript like a normal visitor would, so it clears the
    check reliably. We wait for the rate table (or its heading) to appear
    in the DOM after navigating, since that's the signal that Cloudflare's
    challenge has finished and the real page has rendered. If Cloudflare
    changes/strengthens its challenge in the future, this may need a longer
    wait or a different clearance signal to watch for.

NOTE ON STEALTH:
    Beyond just running a real browser, a few extra touches make it look
    less like an automated one: a `--disable-blink-features=
    AutomationControlled` launch flag, a User-Agent built from the
    browser's own reported version (rather than a hardcoded string that
    quietly goes stale), an Indian viewport/locale/timezone, and an init
    script that hides the `navigator.webdriver` flag Cloudflare (and other
    bot-checks) look for. None of this guarantees success, but it reduces
    the odds of being flagged before the challenge even runs.

Run this manually with:  python fetch_tanishq_gold_rate.py
"""

import os
import re
import sys
import json
import time
from datetime import datetime

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# STEP 0: CONFIGURATION
# ---------------------------------------------------------------------------

SOURCE_URL = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN"

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_tanishq_gold_rate.json")

# Saved only if the wait for the gold rate table times out, so we can see
# what was actually on screen (a Cloudflare challenge, an error page, etc.).
SCREENSHOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_screenshot.png")

# Headers applied on top of the browser context. The User-Agent itself is
# NOT listed here - it's built dynamically from the launched browser's own
# version in fetch_page_html(), so it never goes stale.
REQUEST_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT_SECONDS = 20

# How long to wait (in milliseconds) for Cloudflare's JS challenge to clear
# and the real page - specifically the 22 Kt gold rate table/heading - to
# show up in the DOM. Cloudflare challenges usually resolve in a few
# seconds, but shared/cloud IPs (like GitHub Actions runners) can take
# longer, so this is deliberately generous.
CLOUDFLARE_WAIT_TIMEOUT_MS = 45_000

# Matches either the table's known CSS class, or (as a fallback, in case
# that class name ever changes) a heading that mentions "22 Kt Gold Rate" -
# the same two-tier approach used later by find_22kt_table().
CLOUDFLARE_CLEARED_SELECTOR = (
    "table.goldrate-table-22kt, "
    "h1:has-text('22 Kt Gold Rate'), h2:has-text('22 Kt Gold Rate'), "
    "h3:has-text('22 Kt Gold Rate'), h4:has-text('22 Kt Gold Rate')"
)

# Retry settings for transient failures (a Cloudflare challenge that didn't
# clear in time, a navigation hiccup, or a momentarily malformed page).
MAX_FETCH_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 8


class RetryableFetchError(RuntimeError):
    """
    A timeout/navigation/parsing failure that's worth retrying, since it
    may just be a transient Cloudflare challenge or network hiccup.

    Deliberately NOT used for setup/environment problems (e.g. missing
    Playwright browser binaries) - those will fail the same way every time,
    so retrying them would just waste 24 seconds before giving up anyway.
    """


def fetch_page_html(url: str) -> str:
    """
    STEP 1: Use a headless Chromium browser (via Playwright), disguised to
    look like a normal desktop Chrome visitor, to load the gold rate page
    and return the fully-rendered HTML.

    A real browser is used instead of `requests`/`cloudscraper` because it
    actually executes Cloudflare's JavaScript challenge like a normal
    visitor would (see the "NOTE ON CLOUDFLARE" comment at the top of this
    file). The extra stealth touches (see "NOTE ON STEALTH") make it less
    likely to be flagged as automated before the challenge even runs.

    Raises RetryableFetchError for timeout/navigation failures (worth
    retrying), or a plain RuntimeError for setup problems like a missing
    browser install (not worth retrying).
    """
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except PlaywrightError as e:
            # Launch failures are almost always an environment/setup issue
            # (e.g. the browser binary was never downloaded) - retrying
            # won't fix that, so this is a plain, non-retryable RuntimeError.
            raise RuntimeError(
                "Failed to launch the headless Chromium browser.\n"
                f"Details: {e}\n"
                "Make sure Playwright's browser binaries are installed: "
                "playwright install --with-deps chromium"
            )

        try:
            # Build the User-Agent from the browser's own reported version
            # instead of hardcoding one, so it never quietly goes stale.
            chrome_version = browser.version
            dynamic_user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
            )

            context = browser.new_context(
                user_agent=dynamic_user_agent,
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                extra_http_headers={"Accept-Language": REQUEST_HEADERS["Accept-Language"]},
            )
            # Headless Chromium normally reports navigator.webdriver = true,
            # a well-known automation tell that bot-checks look for.
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()

            try:
                page.goto(
                    url,
                    timeout=REQUEST_TIMEOUT_SECONDS * 1000,
                    wait_until="domcontentloaded",
                )
            except PlaywrightError as e:
                raise RetryableFetchError(
                    f"Failed to navigate to the gold rate page.\nURL: {url}\nDetails: {e}"
                )

            try:
                page.wait_for_selector(
                    CLOUDFLARE_CLEARED_SELECTOR, timeout=CLOUDFLARE_WAIT_TIMEOUT_MS
                )
            except PlaywrightTimeoutError:
                # Capture whatever is currently on screen (Cloudflare
                # challenge, error page, etc.) to help diagnose why the
                # wait timed out. Best-effort - a screenshot failure
                # shouldn't hide the real error below.
                screenshot_note = ""
                try:
                    page.screenshot(path=SCREENSHOT_FILE)
                    screenshot_note = f" A screenshot was saved to '{SCREENSHOT_FILE}'."
                except PlaywrightError:
                    pass

                raise RetryableFetchError(
                    f"Timed out after {CLOUDFLARE_WAIT_TIMEOUT_MS // 1000}s waiting for "
                    "the 22 Kt gold rate table to appear on the page. Either Cloudflare's "
                    "bot-check didn't clear in time, or Tanishq has redesigned the page."
                    + screenshot_note
                )

            return page.content()
        finally:
            browser.close()


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
            raise RetryableFetchError(
                "Found the '1 G' row, but couldn't find a rupee amount in "
                f"its 'Today' cell. Raw cell text was: {today_cell_text!r}"
            )

        return price

    raise RetryableFetchError("Could not find a '1 G' row inside the 22 Kt Gold Rate table.")


def fetch_todays_rate() -> float:
    """
    STEPS 1-4: Fetch the page, parse it, and extract today's 1g/22K price.

    Bundled into one function so main() can retry the whole thing as a
    unit - a transient Cloudflare challenge can just as easily show up as
    a missing table (STEP 3) as it can a fetch timeout (STEP 1).
    """
    html = fetch_page_html(SOURCE_URL)
    soup = BeautifulSoup(html, "html.parser")

    table = find_22kt_table(soup)
    if table is None:
        raise RetryableFetchError(
            "Could not find the '22 Kt Gold Rate' table on the page. "
            "Tanishq may have redesigned this page - the script's "
            "selectors in find_22kt_table() will need updating."
        )

    return extract_today_price_for_1g(table)


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
        # STEPS 1-4: Fetch, parse, and extract today's rate - retrying up
        # to MAX_FETCH_ATTEMPTS times on transient timeout/navigation/
        # parsing errors (e.g. a Cloudflare challenge that didn't clear in
        # time). Setup problems like a missing browser install raise a
        # plain RuntimeError instead, which skips the retry loop entirely.
        # -----------------------------------------------------------------
        rate_inr = None
        last_retryable_error = None
        for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
            print(f"Fetching gold rate page (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {SOURCE_URL}")
            try:
                rate_inr = fetch_todays_rate()
                break
            except RetryableFetchError as e:
                last_retryable_error = e
                if attempt < MAX_FETCH_ATTEMPTS:
                    print(
                        f"Attempt {attempt}/{MAX_FETCH_ATTEMPTS} failed: {e}\n"
                        f"This may be a transient Cloudflare challenge - retrying in "
                        f"{RETRY_DELAY_SECONDS}s...\n"
                    )
                    time.sleep(RETRY_DELAY_SECONDS)

        if rate_inr is None:
            raise last_retryable_error

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
        # not found, browser/Cloudflare issues, etc.) - print a clean
        # message instead of a traceback, and do NOT touch the existing
        # JSON file.
        print(f"\nERROR: {e}")
        sys.exit(1)
    except Exception as e:
        # Catch-all safety net so the script never crashes with a raw,
        # confusing traceback.
        print(f"\nERROR: An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
