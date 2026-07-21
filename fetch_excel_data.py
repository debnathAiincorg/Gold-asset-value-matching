"""
fetch_excel_data.py

Purpose:
    Once a day, log into Microsoft using an Azure "app registration" (a service
    account for apps, not a person), ask Microsoft Graph API for the contents
    of the "Gold Inventory.xlsx" file stored on SharePoint, and save that data
    as a local JSON file (fetch_excel_data.json).

How it works, in plain English:
    1. Read your Azure app's secret credentials from a local .env file.
    2. Trade those credentials for a short-lived "access token" (like a
       temporary ID badge) using Microsoft's OAuth2 "client credentials" flow.
       This flow is for app-to-app communication with no human logging in.
    3. Use that token to ask Graph API: "what is the internal ID of the
       'Accounts' SharePoint site on cloudaiorg.sharepoint.com?"
    4. Use the site ID + the file's GUID to ask Graph API for the Excel
       workbook's worksheets, then the data in the sheet we care about.
    5. Turn the raw rows/columns into a list of dictionaries (JSON objects),
       using row 1 as the field names.
    6. Write that list to fetch_excel_data.json, pretty-printed.

Run this manually with:  python fetch_excel_data.py
See the bottom of this file (in the README instructions provided separately)
for how to schedule it to run automatically every day on Windows.
"""

import os
import re
import sys
import json
from datetime import datetime, date, timedelta

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# STEP 0: CONFIGURATION - values you might want to change are all right here.
# ---------------------------------------------------------------------------

# Load variables from a local ".env" file into the environment (os.environ).
# This lets you keep secrets out of the script and out of source control.
load_dotenv()

# --- SharePoint / file details -------------------------------------------
SHAREPOINT_HOSTNAME = "cloudaiorg.sharepoint.com"
SITE_NAME = "Accounts"
FILE_NAME = "Gold Inventory.xlsx"
FILE_GUID = "2009AE34-6542-42C6-9E4F-3E2DDE8D7F84"

# --- Worksheet to read ------------------------------------------------------
# If you already know the sheet/tab name you want, put it here (e.g. "Sheet1").
# Leave as None to just use the FIRST worksheet in the workbook.
# The script will always print all worksheet names first so you can confirm.
SHEET_NAME = "Sheet1"

# --- Output file -------------------------------------------------------------
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_excel_data.json")

# --- Microsoft Graph API base URL --------------------------------------------
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """
    STEP 1: Authenticate with Microsoft using the "client credentials" OAuth2
    flow (app-only login, no user sign-in). Returns an access token string
    that we attach to every subsequent Graph API request.
    """
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    # This is the standard payload format for the client credentials grant.
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }

    response = requests.post(token_url, data=payload)

    if response.status_code != 200:
        # Don't raise a raw traceback - give a clear, human-readable error.
        raise RuntimeError(
            "Failed to authenticate with Microsoft (token request failed).\n"
            f"HTTP status: {response.status_code}\n"
            f"Details: {response.text}\n"
            "Check that AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET "
            "are correct and that the app registration has the right API "
            "permissions (Sites.Read.All or Files.Read.All, with admin consent)."
        )

    token_data = response.json()
    access_token = token_data.get("access_token")

    if not access_token:
        raise RuntimeError(
            "Authentication succeeded but no access_token was returned. "
            f"Response: {token_data}"
        )

    return access_token


def graph_get(url: str, access_token: str) -> dict:
    """
    Small helper: make a GET request to Microsoft Graph API with the access
    token attached, and turn common failures into clear error messages.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 401:
        raise RuntimeError(
            "Graph API rejected the access token (401 Unauthorized). "
            "The token may have expired or the app may lack permissions."
        )
    if response.status_code == 404:
        raise RuntimeError(
            f"Graph API could not find the requested resource (404 Not Found).\n"
            f"URL: {url}\n"
            "Double-check the site name, file name, and file GUID."
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"Graph API request failed.\nURL: {url}\n"
            f"HTTP status: {response.status_code}\nDetails: {response.text}"
        )

    return response.json()


def get_site_id(access_token: str) -> str:
    """
    STEP 2: Ask Graph API for the internal SharePoint "site ID" that
    corresponds to https://cloudaiorg.sharepoint.com/sites/Accounts
    We need this ID before we can look up any files on that site.
    """
    url = f"{GRAPH_BASE_URL}/sites/{SHAREPOINT_HOSTNAME}:/sites/{SITE_NAME}"
    site_data = graph_get(url, access_token)

    site_id = site_data.get("id")
    if not site_id:
        raise RuntimeError(f"Could not find a site ID in the response: {site_data}")

    return site_id


def list_worksheets(site_id: str, access_token: str) -> list:
    """
    STEP 3 (part A): List every worksheet/tab name in the workbook, and print
    them out. This is so you can confirm the exact sheet name to use, since
    you weren't sure of it yet.
    """
    url = f"{GRAPH_BASE_URL}/sites/{site_id}/drive/items/{FILE_GUID}/workbook/worksheets"
    worksheets_data = graph_get(url, access_token)

    worksheets = worksheets_data.get("value", [])
    sheet_names = [ws["name"] for ws in worksheets]

    print("\nWorksheets found in '{}':".format(FILE_NAME))
    for name in sheet_names:
        print(f"  - {name}")
    print()

    return sheet_names


def get_used_range_values(site_id: str, sheet_name: str, access_token: str) -> list:
    """
    STEP 3 (part B): Read the "usedRange" (every cell that actually contains
    data) of the chosen worksheet, and return the raw values as a list of
    rows, where each row is a list of cell values.
    """
    url = (
        f"{GRAPH_BASE_URL}/sites/{site_id}/drive/items/{FILE_GUID}/workbook/"
        f"worksheets('{sheet_name}')/usedRange"
    )
    range_data = graph_get(url, access_token)

    values = range_data.get("values")
    if values is None:
        raise RuntimeError(f"No 'values' found in usedRange response: {range_data}")

    return values


def normalize_header(header) -> str:
    """
    Turn a column header into a simple lowercase form with single spaces
    (e.g. "Product  Details" -> "product details"), so we can reliably find
    key columns like "Vendor Name" even if the sheet's spacing varies.
    """
    return re.sub(r"\s+", " ", str(header).strip()).lower()


def is_blank(value) -> bool:
    """A cell counts as blank if it's None or an empty/whitespace-only string."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def excel_serial_to_iso_date(value):
    """
    Excel stores dates as "serial numbers" - a count of days since
    December 30, 1899 (Excel's epoch, offset by its historical leap-year
    bug). This converts that number into a readable "YYYY-MM-DD" string.
    If the value isn't a number (or is blank), it's returned unchanged.
    """
    if is_blank(value):
        return value

    try:
        serial_number = float(value)
    except (TypeError, ValueError):
        return value

    excel_epoch = date(1899, 12, 30)
    converted_date = excel_epoch + timedelta(days=serial_number)
    return converted_date.strftime("%Y-%m-%d")


def rows_to_json_records(rows: list) -> list:
    """
    STEP 4: Convert raw spreadsheet rows into a list of JSON-friendly
    dictionaries, using the first row as column headers - then clean up
    the result:
      - Fully blank rows are dropped.
      - Stray/junk rows (only a stray value or two, e.g. a leftover formula
        result) are dropped. A row only counts as real data if it has both
        "Product Details" and "Vendor Name" filled in.
      - The "date of Purchase" column is converted from Excel's serial
        number format into a plain "YYYY-MM-DD" string.

    Example:
        rows = [["Name", "Weight"], ["Gold Bar A", 100], ["Gold Bar B", 250]]
        becomes:
        [{"Name": "Gold Bar A", "Weight": 100}, {"Name": "Gold Bar B", "Weight": 250}]
    """
    if not rows:
        return []

    headers = rows[0]
    data_rows = rows[1:]

    # Look up the actual header text for the columns we need to check/convert,
    # matching by normalized name so small spacing differences don't matter.
    normalized_to_actual = {normalize_header(h): h for h in headers}
    product_details_header = normalized_to_actual.get("product details")
    vendor_name_header = normalized_to_actual.get("vendor name")
    purchase_date_header = normalized_to_actual.get("date of purchase")

    records = []
    for row in data_rows:
        # Pair each header with its matching cell value in this row.
        # zip() stops at the shorter of the two, so short rows are handled
        # gracefully instead of raising an IndexError.
        record = {header: value for header, value in zip(headers, row)}

        # Rule 1: skip rows where every field is empty.
        if all(is_blank(v) for v in record.values()):
            continue

        # Rule 3: skip stray/junk rows - a real record must have both
        # "Product Details" and "Vendor Name" filled in.
        product_details_value = record.get(product_details_header)
        vendor_name_value = record.get(vendor_name_header)
        if is_blank(product_details_value) or is_blank(vendor_name_value):
            continue

        # Rule 2: convert the purchase date from an Excel serial number to
        # a readable "YYYY-MM-DD" string.
        if purchase_date_header:
            record[purchase_date_header] = excel_serial_to_iso_date(
                record[purchase_date_header]
            )

        records.append(record)

    return records


def main():
    # -------------------------------------------------------------------
    # STEP A: Load and validate the Azure credentials from environment
    # variables (populated from the .env file by load_dotenv() above).
    # -------------------------------------------------------------------
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")

    if not tenant_id or not client_id or not client_secret:
        print(
            "ERROR: Missing Azure credentials.\n"
            "Please make sure AZURE_TENANT_ID, AZURE_CLIENT_ID and "
            "AZURE_CLIENT_SECRET are set in a .env file next to this script "
            "(see .env.example)."
        )
        sys.exit(1)

    try:
        # ---------------------------------------------------------------
        # STEP B: Authenticate and get an access token.
        # ---------------------------------------------------------------
        print("Authenticating with Microsoft...")
        access_token = get_access_token(tenant_id, client_id, client_secret)
        print("Authentication successful.")

        # ---------------------------------------------------------------
        # STEP C: Look up the SharePoint site ID.
        # ---------------------------------------------------------------
        print(f"Looking up SharePoint site '{SITE_NAME}'...")
        site_id = get_site_id(access_token)
        print(f"Found site ID: {site_id}")

        # ---------------------------------------------------------------
        # STEP D: List worksheets so the sheet name can be confirmed.
        # ---------------------------------------------------------------
        print(f"Reading worksheet list from '{FILE_NAME}'...")
        sheet_names = list_worksheets(site_id, access_token)

        if not sheet_names:
            print("ERROR: No worksheets were found in the workbook.")
            sys.exit(1)

        # Decide which sheet to actually read:
        # use SHEET_NAME if set, otherwise fall back to the first worksheet.
        target_sheet = SHEET_NAME if SHEET_NAME else sheet_names[0]

        if target_sheet not in sheet_names:
            print(
                f"ERROR: SHEET_NAME is set to '{target_sheet}', but that "
                f"sheet was not found. Available sheets: {sheet_names}"
            )
            sys.exit(1)

        print(f"Using worksheet: '{target_sheet}'")

        # ---------------------------------------------------------------
        # STEP E: Read the data out of that worksheet.
        # ---------------------------------------------------------------
        print("Fetching cell data...")
        rows = get_used_range_values(site_id, target_sheet, access_token)

        # ---------------------------------------------------------------
        # STEP F: Convert rows into a list of JSON objects (dictionaries).
        # ---------------------------------------------------------------
        records = rows_to_json_records(rows)

        # ---------------------------------------------------------------
        # STEP G: Save the result to a local JSON file, pretty-printed.
        # ---------------------------------------------------------------
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)

        # ---------------------------------------------------------------
        # STEP H: Print a success message with row count and timestamp.
        # ---------------------------------------------------------------
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"\nSUCCESS: Fetched {len(records)} row(s) from '{target_sheet}' "
            f"and saved to '{OUTPUT_FILE}' at {timestamp}."
        )

    except RuntimeError as e:
        # Errors we raised ourselves above (auth failures, missing site/file,
        # bad token, etc.) - print a clean message instead of a traceback.
        print(f"\nERROR: {e}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        # Network-level problems (no internet, DNS failure, timeout, etc.)
        print(f"\nERROR: A network problem occurred while calling Microsoft Graph API: {e}")
        sys.exit(1)
    except Exception as e:
        # Catch-all safety net so the script never crashes with a raw,
        # confusing traceback. This should rarely trigger if the code above
        # is correct, but protects against anything unexpected.
        print(f"\nERROR: An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
