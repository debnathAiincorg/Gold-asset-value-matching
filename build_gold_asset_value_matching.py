"""
build_gold_asset_value_matching.py

Purpose:
    Build the site's home page (Gold_asset_value_matching.html): a "National calculation" summary
    of the gold we currently hold (available weight, today's Tanishq rate,
    and total value), plus an empty "Kolkata calculation" section reserved
    for future work. Also adds a nav bar linking to the full inventory
    dashboard (gold_all_ditails_with_table.html).

How it works, in plain English:
    1. Load the same two JSON files build_gold_all_ditails_with_table.py uses:
       fetch_excel_data.json (inventory) and fetch_tanishq_gold_rate.json
       (today's rate).
    2. Add up the net weight of every item that ISN'T marked "sold" in its
       Notes/Remarks - that's the gold we actually still hold, using the
       same "sold" convention as build_gold_all_ditails_with_table.py's isSold flag.
    3. Multiply that weight by today's rate to get its current total value.
    4. Fill in an HTML template with those numbers and save it as
       Gold_asset_value_matching.html.

Run this manually with:  python build_gold_asset_value_matching.py
Then double-click Gold_asset_value_matching.html (or open it in a browser) to view it.
"""

import os
import sys
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# STEP 0: CONFIGURATION - file paths, all relative to this script's folder.
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INVENTORY_FILE = os.path.join(SCRIPT_DIR, "fetch_excel_data.json")
GOLD_RATE_FILE = os.path.join(SCRIPT_DIR, "fetch_tanishq_gold_rate.json")

# Nav bar filenames - kept as named constants (rather than hardcoded inside
# HTML_TEMPLATE) so the site's pages' cross-links can't silently drift out of
# sync with build_gold_all_ditails_with_table.py / compair_to_other_rate.html
# if any of them is ever renamed again.
HOME_PAGE = "Gold_asset_value_matching.html"
INVENTORY_PAGE = "gold_all_ditails_with_table.html"
COMPARE_PAGE = "compair_to_other_rate.html"
OUTPUT_FILE = os.path.join(SCRIPT_DIR, HOME_PAGE)


def load_json_file(path: str, description: str):
    """
    Load a JSON file from disk, with a clear error message if it's missing
    or not valid JSON (instead of a raw traceback).
    """
    if not os.path.exists(path):
        raise RuntimeError(
            f"Could not find {description} at:\n  {path}\n"
            "Make sure that file exists in the same folder as this script."
        )

    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"{description} exists but isn't valid JSON ({path}).\n"
                f"Details: {e}"
            )


def safe_number(value, default: float = 0.0) -> float:
    """
    Convert a spreadsheet cell into a plain number, gracefully handling
    blanks, None, or anything that isn't actually numeric.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return default
        try:
            return float(stripped)
        except ValueError:
            return default
    return default


def safe_text(value) -> str:
    """Turn a possibly-missing/None cell into a plain (possibly empty) string."""
    if value is None:
        return ""
    return str(value).strip()


def format_inr(amount: float) -> str:
    """
    Format a number as an Indian-Rupee string with Indian-style comma
    grouping - the last 3 digits, then groups of 2 (e.g. 105560 -> "1,05,560"),
    always with 2 decimal places, e.g. "₹1,05,560.00".
    """
    is_negative = amount < 0
    amount = round(abs(amount), 2)

    whole_part = int(amount)
    paise = int(round((amount - whole_part) * 100))
    if paise == 100:  # rounding edge case, e.g. 9.995 -> "10.00"
        whole_part += 1
        paise = 0

    digits = str(whole_part)
    if len(digits) <= 3:
        grouped = digits
    else:
        last_three = digits[-3:]
        remaining = digits[:-3]
        groups_of_two = []
        while len(remaining) > 2:
            groups_of_two.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups_of_two.insert(0, remaining)
        grouped = ",".join(groups_of_two + [last_three])

    result = f"₹{grouped}.{paise:02d}"
    return f"-{result}" if is_negative else result


def is_sold(record: dict) -> bool:
    """
    Same "sold" convention as build_gold_all_ditails_with_table.py: an item counts as
    sold if either free-text field mentions it.
    """
    notes = safe_text(record.get("Notes")).lower()
    remarks = safe_text(record.get("Remarks")).lower()
    return "sold" in notes or "sold" in remarks


def compute_available_gold_grams(inventory: list) -> float:
    """STEP 2: Total net weight of every item we still hold (not sold)."""
    return sum(
        safe_number(record.get("Net Weight Gm"))
        for record in inventory
        if not is_sold(record)
    )


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Gold Value Matching</title>
<style>
  :root {
    color-scheme: light;
    --page-plane:     #f9f9f7;
    --surface-1:      #fcfcfb;
    --surface-2:      #f0efec;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --gridline:       #e1e0d9;
    --border:         rgba(11,11,11,0.10);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --page-plane:     #0d0d0d;
      --surface-1:      #1a1a19;
      --surface-2:      #232322;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --gridline:       #2c2c2a;
      --border:         rgba(255,255,255,0.10);
    }
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    background: var(--page-plane);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    line-height: 1.4;
  }

  nav.top-nav {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 28px;
    background: var(--surface-1);
    border-bottom: 1px solid var(--border);
  }
  nav.top-nav .brand {
    font-weight: 600;
    font-size: 16px;
  }
  nav.top-nav .nav-links {
    display: flex;
    gap: 8px;
  }
  nav.top-nav .nav-link {
    color: var(--text-primary);
    text-decoration: none;
    font-size: 14px;
    font-weight: 500;
    padding: 8px 14px;
    border-radius: 6px;
    border: 1px solid var(--gridline);
    background: var(--surface-2);
  }
  nav.top-nav .nav-link:hover {
    border-color: var(--text-muted);
  }
  nav.top-nav .nav-link.active {
    background: var(--text-primary);
    color: var(--page-plane);
    border-color: var(--text-primary);
  }

  .page {
    max-width: 1300px;
    width: 92%;
    margin: 0 auto;
    padding: 16px 0 60px;
  }

  section.calc-section {
    text-align: center;
    margin-bottom: 28px;
  }
  section.calc-section h1, section.calc-section h2 {
    margin: 0 0 6px;
    font-size: 24px;
    font-weight: 600;
  }
  .calc-subtitle {
    margin: 0 0 20px;
    color: var(--text-secondary);
    font-size: 14.42px;
  }

  .kolkata-section {
    min-height: 220px;
  }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 18px;
    text-align: left;
  }
  .stat-tile {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 26px 28px;
  }
  .stat-label {
    font-size: 15px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-secondary);
    margin-bottom: 8px;
  }
  .stat-value {
    font-size: 30px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
</style>
</head>
<body>
  <nav class="top-nav">
    <span class="brand">Gold Value Matching</span>
    <div class="nav-links">
      <a href="__HOME_PAGE__" class="nav-link active">Home</a>
      <a href="__INVENTORY_PAGE__" class="nav-link">Our Gold full details</a>
      <a href="__COMPARE_PAGE__" class="nav-link">Compare to Other Rates</a>
    </div>
  </nav>

  <div class="page">
    <section class="calc-section">
      <h1>National calculation</h1>
      <p class="calc-subtitle">Last updated __DATE__ at __TIME__ IST</p>
      <div class="stat-grid">
        <div class="stat-tile">
          <div class="stat-label">Available Gold</div>
          <div class="stat-value">__AVAILABLE_GOLD__</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Date</div>
          <div class="stat-value">__DATE__</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Tanishq National Rate</div>
          <div class="stat-value">__TANISHQ_RATE__</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Total Gold Value</div>
          <div class="stat-value">__TOTAL_VALUE__</div>
        </div>
      </div>
    </section>

    <section class="calc-section kolkata-section">
      <h2>Kolkata calculation</h2>
    </section>
  </div>
</body>
</html>
"""


def build_html(available_gold_grams: float, todays_rate: float, total_value: float) -> str:
    """STEP 4: Fill in the HTML template above with the computed numbers."""
    # Explicitly convert to IST (UTC+5:30) rather than relying on the
    # server's local timezone - this runs on GitHub Actions (UTC) as well
    # as local machines, and the displayed time should always read as IST.
    now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    # "%d %B" + lstrip("0") gives "3 August" instead of a leading-zero "03
    # August" - day-of-month only, no year, matching the requested format.
    date_str = now_ist.strftime("%d %B").lstrip("0")
    hour_12 = now_ist.strftime("%I").lstrip("0") or "12"
    time_str = f"{hour_12}:{now_ist.strftime('%M %p')}"

    html = HTML_TEMPLATE
    html = html.replace("__HOME_PAGE__", HOME_PAGE)
    html = html.replace("__INVENTORY_PAGE__", INVENTORY_PAGE)
    html = html.replace("__COMPARE_PAGE__", COMPARE_PAGE)
    html = html.replace("__DATE__", date_str)
    html = html.replace("__TIME__", time_str)
    html = html.replace("__AVAILABLE_GOLD__", f"{available_gold_grams:.3f} g")
    html = html.replace("__TANISHQ_RATE__", f"{format_inr(todays_rate)}/gram")
    html = html.replace("__TOTAL_VALUE__", format_inr(total_value))

    return html


def main():
    try:
        # -----------------------------------------------------------------
        # STEP 1: Load the two input JSON files.
        # -----------------------------------------------------------------
        inventory = load_json_file(INVENTORY_FILE, "the gold inventory file (fetch_excel_data.json)")
        rate_info = load_json_file(GOLD_RATE_FILE, "the gold rate file (fetch_tanishq_gold_rate.json)")

        todays_rate = safe_number(rate_info.get("rate_inr"))
        if todays_rate <= 0:
            raise RuntimeError(
                "fetch_tanishq_gold_rate.json doesn't have a usable 'rate_inr' value. "
                "Re-run fetch_tanishq_gold_rate.py to refresh it."
            )

        if not isinstance(inventory, list) or not inventory:
            raise RuntimeError("fetch_excel_data.json is empty or isn't a list of records.")

        # -----------------------------------------------------------------
        # STEP 2 + 3: Calculate available gold weight and its total value.
        # -----------------------------------------------------------------
        available_gold_grams = compute_available_gold_grams(inventory)
        total_value = available_gold_grams * todays_rate

        # -----------------------------------------------------------------
        # STEP 4: Build the HTML page and save it.
        # -----------------------------------------------------------------
        html = build_html(available_gold_grams, todays_rate, total_value)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(html)

        # -----------------------------------------------------------------
        # STEP 5: Print a confirmation to the console.
        # -----------------------------------------------------------------
        def console_money(amount: float) -> str:
            return format_inr(amount).replace("₹", "Rs. ")

        print(f"SUCCESS: Home page written to '{OUTPUT_FILE}'.")
        print(f"  Available Gold:   {available_gold_grams:.3f} g")
        print(f"  Tanishq rate:     {console_money(todays_rate)}/gram")
        print(f"  Total gold value: {console_money(total_value)}")

    except RuntimeError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
