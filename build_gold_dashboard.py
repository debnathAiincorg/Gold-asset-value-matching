"""
build_gold_dashboard.py

Purpose:
    Read our gold purchase records (fetch_excel_data.json) and today's market
    rate (fetch_tanishq_gold_rate.json), work out how much profit or loss we'd have
    if we sold everything at today's rate, and generate a single self-
    contained HTML file (Gold_asset_value_matching.html) you can double-click to view
    the results in a browser.

How it works, in plain English:
    1. Load both JSON files.
    2. For every purchase record, calculate:
       - "Our Amount"    = what we actually paid for the raw gold
       - "Today's Value" = what that same weight of gold is worth today
       - "Profit/Loss"   = the difference between the two
    3. Add up those numbers across every record for the summary cards.
    4. Build one HTML file with those summary cards plus a table (with
       sorting and searching built in via a bit of JavaScript), and save it.

Run this manually with:  python build_gold_dashboard.py
Then double-click Gold_asset_value_matching.html (or open it in a browser) to view it.
"""

import os
import sys
import json
from datetime import datetime

# ---------------------------------------------------------------------------
# STEP 0: CONFIGURATION - file paths, all relative to this script's folder.
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INVENTORY_FILE = os.path.join(SCRIPT_DIR, "fetch_excel_data.json")
GOLD_RATE_FILE = os.path.join(SCRIPT_DIR, "fetch_tanishq_gold_rate.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "Gold_asset_value_matching.html")


def load_json_file(path: str, description: str):
    """
    STEP 1: Load a JSON file from disk, with a clear error message if it's
    missing or not valid JSON (instead of a raw traceback).
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
    blanks, None, or anything that isn't actually numeric - so a stray
    blank cell can't crash the whole calculation.
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


def compute_rows(inventory: list, todays_rate: float) -> list:
    """
    STEP 2: Work out Our Amount / Today's Value / Profit-Loss for every
    purchase record, and package each one up as a plain dictionary that's
    easy to both total up (STEP 3) and hand to the HTML template.
    """
    rows = []

    for record in inventory:
        net_weight_gm = safe_number(record.get("Net Weight Gm"))
        purchase_rate = safe_number(record.get("Purchase gold Rate"))
        purity_karat = safe_number(record.get("Purity Karat"))

        # "Our Amount" is the raw gold cost only (weight x rate we paid) -
        # deliberately NOT the "Total Value" column, which also bakes in
        # making charges.
        our_amount = net_weight_gm * purchase_rate

        # "Today's Value" applies today's 22K rate to every item's weight,
        # regardless of that item's own purity - per the requested logic.
        todays_value = net_weight_gm * todays_rate

        profit_loss = todays_value - our_amount
        # Guard against dividing by zero if "our_amount" ever comes out to 0.
        profit_loss_pct = (profit_loss / our_amount * 100) if our_amount else 0.0

        notes = safe_text(record.get("Notes"))
        remarks = safe_text(record.get("Remarks"))
        # An item counts as "sold" if either free-text field mentions it -
        # sold items are no longer part of current holdings, so the
        # dashboard visually grays them out (see the CSS ".sold-row" rule).
        is_sold = "sold" in notes.lower() or "sold" in remarks.lower()

        rows.append({
            "product": safe_text(record.get("Product  Details")),
            "vendor": safe_text(record.get("Vendor Name")),
            "weight": round(net_weight_gm, 3),
            "purity": purity_karat,
            "rate": round(purchase_rate, 2),
            "ourAmount": round(our_amount, 2),
            "todaysValue": round(todays_value, 2),
            "profitLoss": round(profit_loss, 2),
            "profitLossPct": round(profit_loss_pct, 2),
            "notes": notes,
            "remarks": remarks,
            "isSold": is_sold,
        })

    return rows


def compute_totals(rows: list) -> dict:
    """STEP 3: Add up the per-item numbers into dashboard-wide totals."""
    total_our_amount = sum(r["ourAmount"] for r in rows)
    total_todays_value = sum(r["todaysValue"] for r in rows)
    total_profit_loss = total_todays_value - total_our_amount
    total_profit_loss_pct = (
        total_profit_loss / total_our_amount * 100 if total_our_amount else 0.0
    )

    return {
        "total_items": len(rows),
        "sold_items": sum(1 for r in rows if r["isSold"]),
        "total_our_amount": total_our_amount,
        "total_todays_value": total_todays_value,
        "total_profit_loss": total_profit_loss,
        "total_profit_loss_pct": total_profit_loss_pct,
    }


def format_inr(amount: float) -> str:
    """
    Format a number as an Indian-Rupee string with Indian-style comma
    grouping - the last 3 digits, then groups of 2 (e.g. 105560 -> "1,05,560"),
    always with 2 decimal places, e.g. "₹1,05,560.00".
    """
    is_negative = amount < 0
    amount = round(abs(amount), 2)

    whole_part = int(amount)
    # Get exactly 2 decimal digits without floating-point rounding surprises.
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


def format_karat(value: float) -> str:
    """Show a purity number as e.g. "22K" (or "22.5K" if it isn't a whole number)."""
    if value == int(value):
        return f"{int(value)}K"
    return f"{value}K"


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Gold Inventory Dashboard</title>
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
    --good-text:      #006300;
    --good-tint:      #eaf6ea;
    --critical-text:  #d03b3b;
    --critical-tint:  #fbecec;
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
      --good-text:      #0ca30c;
      --good-tint:      #123018;
      --critical-text:  #e66767;
      --critical-tint:  #341616;
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

  .dashboard {
    max-width: 1200px;
    margin: 0 auto;
    padding: 32px 20px 60px;
  }

  header.dash-header h1 {
    margin: 0 0 4px;
    font-size: 28px;
    font-weight: 600;
  }
  header.dash-header .subtitle {
    margin: 0;
    color: var(--text-secondary);
    font-size: 14px;
  }

  .stat-grid {
    margin-top: 24px;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 14px;
  }
  .stat-tile {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }
  .stat-label {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 6px;
  }
  .stat-value {
    font-size: 24px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .stat-tile.positive .stat-value { color: var(--good-text); }
  .stat-tile.negative .stat-value { color: var(--critical-text); }
  .stat-delta { font-size: 15px; font-weight: 500; margin-left: 4px; }
  .stat-caption {
    margin-top: 6px;
    font-size: 12px;
    color: var(--text-muted);
  }

  .table-section {
    margin-top: 30px;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }

  .table-controls {
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    padding: 14px 18px;
    border-bottom: 1px solid var(--gridline);
  }
  #searchInput {
    flex: 1 1 240px;
    min-width: 200px;
    padding: 8px 12px;
    border-radius: 6px;
    border: 1px solid var(--gridline);
    background: var(--page-plane);
    color: var(--text-primary);
    font-size: 14px;
  }
  .toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 14px;
    color: var(--text-secondary);
    white-space: nowrap;
    cursor: pointer;
  }
  .row-count {
    font-size: 13px;
    color: var(--text-muted);
    white-space: nowrap;
  }

  .table-scroll { overflow-x: auto; }

  table { width: 100%; border-collapse: collapse; font-size: 14px; min-width: 900px; }
  thead th {
    text-align: left;
    padding: 10px 14px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-secondary);
    border-bottom: 1px solid var(--gridline);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
  }
  thead th:hover { color: var(--text-primary); }
  thead th.num, td.num { text-align: right; }
  .sort-indicator { font-size: 10px; margin-left: 4px; color: var(--text-muted); }

  tbody td {
    padding: 10px 14px;
    border-bottom: 1px solid var(--gridline);
    font-variant-numeric: tabular-nums;
  }
  tbody tr:last-child td { border-bottom: none; }

  tr.sold-row { opacity: 0.55; font-style: italic; }

  .pl-cell.positive { color: var(--good-text); font-weight: 600; }
  .pl-cell.negative { color: var(--critical-text); font-weight: 600; }
  .pl-pct { font-weight: 400; font-size: 12.5px; margin-left: 4px; }

  .empty-state {
    padding: 40px 20px;
    text-align: center;
    color: var(--text-muted);
  }

  footer.dash-footer {
    margin-top: 20px;
    font-size: 12px;
    color: var(--text-muted);
  }
</style>
</head>
<body>
<div class="dashboard">

  <header class="dash-header">
    <h1>Gold Inventory Dashboard</h1>
    <p class="subtitle">__SUBTITLE__</p>
  </header>

  <section class="stat-grid">
    <div class="stat-tile">
      <div class="stat-label">Total Items</div>
      <div class="stat-value">__TOTAL_ITEMS__</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">Total Our Amount</div>
      <div class="stat-value">__TOTAL_OUR_AMOUNT__</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">Total Today's Value</div>
      <div class="stat-value">__TOTAL_TODAYS_VALUE__</div>
    </div>
    <div class="stat-tile __TOTAL_PL_CLASS__">
      <div class="stat-label">Total Profit / Loss</div>
      <div class="stat-value">__TOTAL_PL_AMOUNT__<span class="stat-delta">(__TOTAL_PL_PCT__%)</span></div>
      __SOLD_CAPTION__
    </div>
  </section>

  <section class="table-section">
    <div class="table-controls">
      <input type="search" id="searchInput" placeholder="Search product, vendor, or notes...">
      <label class="toggle"><input type="checkbox" id="hideSoldCheckbox"> Hide sold items</label>
      <span class="row-count" id="rowCount"></span>
    </div>
    <div class="table-scroll">
      <table id="inventoryTable">
        <thead>
          <tr>
            <th data-key="product" tabindex="0">Product Details<span class="sort-indicator"></span></th>
            <th data-key="vendor" tabindex="0">Vendor Name<span class="sort-indicator"></span></th>
            <th data-key="weight" class="num" tabindex="0">Net Weight (g)<span class="sort-indicator"></span></th>
            <th data-key="purity" class="num" tabindex="0">Purity<span class="sort-indicator"></span></th>
            <th data-key="rate" class="num" tabindex="0">Purchase Rate<span class="sort-indicator"></span></th>
            <th data-key="ourAmount" class="num" tabindex="0">Our Amount<span class="sort-indicator"></span></th>
            <th data-key="todaysValue" class="num" tabindex="0">Today's Value<span class="sort-indicator"></span></th>
            <th data-key="profitLoss" class="num" tabindex="0">Profit / Loss<span class="sort-indicator"></span></th>
            <th data-key="notes" tabindex="0">Notes<span class="sort-indicator"></span></th>
          </tr>
        </thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
  </section>

  <footer class="dash-footer">Generated by build_gold_dashboard.py on __GENERATED_AT__</footer>
</div>

<script type="application/json" id="row-data">__ROWS_JSON__</script>
<script>
(function () {
  "use strict";

  var rows = JSON.parse(document.getElementById("row-data").textContent);
  var tableBody = document.getElementById("tableBody");
  var searchInput = document.getElementById("searchInput");
  var hideSoldCheckbox = document.getElementById("hideSoldCheckbox");
  var rowCountEl = document.getElementById("rowCount");

  var sortKey = null;
  var sortAscending = true;

  var inrFormatter = new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  function formatINR(amount) {
    var sign = amount < 0 ? "-" : "";
    return sign + "\\u20b9" + inrFormatter.format(Math.abs(amount));
  }

  function formatKarat(value) {
    return (Number.isInteger(value) ? value : value.toFixed(1)) + "K";
  }

  // Build one <tr> for a row of data. We use textContent (never innerHTML)
  // for anything that came from the spreadsheet, since that text is user
  // data and could contain characters that would otherwise be misread as
  // HTML markup.
  function buildRow(row) {
    var tr = document.createElement("tr");
    if (row.isSold) {
      tr.className = "sold-row";
    }

    function addCell(text, extraClass) {
      var td = document.createElement("td");
      if (extraClass) td.className = extraClass;
      td.textContent = text;
      tr.appendChild(td);
      return td;
    }

    addCell(row.product);
    addCell(row.vendor);
    addCell(row.weight.toFixed(3), "num");
    addCell(formatKarat(row.purity), "num");
    addCell(formatINR(row.rate), "num");
    addCell(formatINR(row.ourAmount), "num");
    addCell(formatINR(row.todaysValue), "num");

    var plTd = document.createElement("td");
    plTd.className = "num pl-cell " + (row.profitLoss >= 0 ? "positive" : "negative");
    var arrow = row.profitLoss >= 0 ? "\\u25b2 " : "\\u25bc ";
    plTd.textContent = arrow + formatINR(row.profitLoss);
    var pctSpan = document.createElement("span");
    pctSpan.className = "pl-pct";
    pctSpan.textContent = "(" + (row.profitLossPct >= 0 ? "+" : "") + row.profitLossPct.toFixed(2) + "%)";
    plTd.appendChild(pctSpan);
    tr.appendChild(plTd);

    addCell(row.notes);

    return tr;
  }

  function currentRows() {
    var term = searchInput.value.trim().toLowerCase();
    var hideSold = hideSoldCheckbox.checked;

    var filtered = rows.filter(function (row) {
      if (hideSold && row.isSold) return false;
      if (!term) return true;
      var haystack = (row.product + " " + row.vendor + " " + row.notes).toLowerCase();
      return haystack.indexOf(term) !== -1;
    });

    if (sortKey) {
      filtered.sort(function (a, b) {
        var av = a[sortKey];
        var bv = b[sortKey];
        if (typeof av === "string") {
          av = av.toLowerCase();
          bv = bv.toLowerCase();
        }
        if (av < bv) return sortAscending ? -1 : 1;
        if (av > bv) return sortAscending ? 1 : -1;
        return 0;
      });
    }

    return filtered;
  }

  function render() {
    var visibleRows = currentRows();

    tableBody.textContent = "";
    if (visibleRows.length === 0) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 9;
      td.className = "empty-state";
      td.textContent = "No items match your search.";
      tr.appendChild(td);
      tableBody.appendChild(tr);
    } else {
      visibleRows.forEach(function (row) {
        tableBody.appendChild(buildRow(row));
      });
    }

    rowCountEl.textContent = "Showing " + visibleRows.length + " of " + rows.length + " items";
  }

  function updateSortIndicators() {
    var headers = document.querySelectorAll("#inventoryTable thead th");
    headers.forEach(function (th) {
      var indicator = th.querySelector(".sort-indicator");
      if (th.getAttribute("data-key") === sortKey) {
        indicator.textContent = sortAscending ? "\\u25b2" : "\\u25bc";
      } else {
        indicator.textContent = "";
      }
    });
  }

  function handleHeaderActivate(th) {
    var key = th.getAttribute("data-key");
    if (sortKey === key) {
      sortAscending = !sortAscending;
    } else {
      sortKey = key;
      sortAscending = true;
    }
    updateSortIndicators();
    render();
  }

  document.querySelectorAll("#inventoryTable thead th").forEach(function (th) {
    th.addEventListener("click", function () { handleHeaderActivate(th); });
    th.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        handleHeaderActivate(th);
      }
    });
  });

  searchInput.addEventListener("input", render);
  hideSoldCheckbox.addEventListener("change", render);

  render();
})();
</script>
</body>
</html>
"""


def build_html(rows: list, totals: dict, todays_rate: float, rate_info: dict) -> str:
    """
    STEP 4: Fill in the HTML template above with the computed numbers and
    the per-item row data (embedded as JSON for the page's own JavaScript
    to sort/filter/render).
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    rate_fetched_date = safe_text(rate_info.get("date_fetched")) or "unknown date"

    subtitle = (
        f"Generated on {today_str} &middot; "
        f"Using 22K rate {format_inr(todays_rate)}/gram (fetched {rate_fetched_date})"
    )

    total_pl = totals["total_profit_loss"]
    total_pl_class = "positive" if total_pl >= 0 else "negative"
    total_pl_amount = format_inr(total_pl)
    total_pl_pct = totals["total_profit_loss_pct"]
    total_pl_pct_str = f"{'+' if total_pl_pct >= 0 else ''}{total_pl_pct:.2f}"

    sold_caption = ""
    if totals["sold_items"]:
        sold_caption = (
            f'<div class="stat-caption">Includes {totals["sold_items"]} '
            f'sold item(s) still counted in these totals</div>'
        )

    # Embed the row data as JSON inside a <script type="application/json"> tag.
    # Escaping "</" as "<\/" stops a stray "</script" inside the data (e.g. in
    # a Notes field) from prematurely closing the tag.
    rows_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")

    html = HTML_TEMPLATE
    html = html.replace("__SUBTITLE__", subtitle)
    html = html.replace("__TOTAL_ITEMS__", str(totals["total_items"]))
    html = html.replace("__TOTAL_OUR_AMOUNT__", format_inr(totals["total_our_amount"]))
    html = html.replace("__TOTAL_TODAYS_VALUE__", format_inr(totals["total_todays_value"]))
    html = html.replace("__TOTAL_PL_CLASS__", total_pl_class)
    html = html.replace("__TOTAL_PL_AMOUNT__", total_pl_amount)
    html = html.replace("__TOTAL_PL_PCT__", total_pl_pct_str)
    html = html.replace("__SOLD_CAPTION__", sold_caption)
    html = html.replace("__GENERATED_AT__", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    html = html.replace("__ROWS_JSON__", rows_json)

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
        # STEP 2 + 3: Calculate per-item and total figures.
        # -----------------------------------------------------------------
        rows = compute_rows(inventory, todays_rate)
        totals = compute_totals(rows)

        # -----------------------------------------------------------------
        # STEP 4: Build the HTML page and save it.
        # -----------------------------------------------------------------
        html = build_html(rows, totals, todays_rate, rate_info)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(html)

        # -----------------------------------------------------------------
        # STEP 5: Print a confirmation, including the totals, to the console.
        # -----------------------------------------------------------------
        # Note: we print "Rs." here instead of the "₹" symbol used inside the
        # HTML/JSON files, since some Windows terminals use a default
        # codepage that can't display "₹" and would crash this print call.
        def console_money(amount: float) -> str:
            return format_inr(amount).replace("₹", "Rs. ")

        pl_word = "PROFIT" if totals["total_profit_loss"] >= 0 else "LOSS"
        print(f"SUCCESS: Dashboard written to '{OUTPUT_FILE}'.")
        print(f"  Items processed:     {totals['total_items']} ({totals['sold_items']} marked sold)")
        print(f"  Total Our Amount:    {console_money(totals['total_our_amount'])}")
        print(f"  Total Today's Value: {console_money(totals['total_todays_value'])}")
        print(
            f"  Total {pl_word}:          {console_money(abs(totals['total_profit_loss']))} "
            f"({totals['total_profit_loss_pct']:.2f}%)"
        )

    except RuntimeError as e:
        # Errors we raised ourselves above - print a clean message instead
        # of a raw traceback.
        print(f"\nERROR: {e}")
        sys.exit(1)
    except Exception as e:
        # Catch-all safety net for anything unexpected.
        print(f"\nERROR: An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
