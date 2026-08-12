"""Stage 2 end-to-end check: does a PDF receipt now classify instead of failing?

Drives the three real API calls a browser makes — login, receipt upload, claim submission — against
a running server, then prints the ``documentClassification`` block the fix is meant to populate.

    python scripts/test_pdf_classification.py path/to/meals_invoice_v2.pdf

Credentials come from the environment so nothing is hard-coded here. Either log in:

    $env:TEST_EMAIL="employee@example.com"; $env:TEST_PASSWORD="..."

or skip the login with an access token you already hold (Cognito's expire in an hour):

    $env:TEST_TOKEN="eyJraWQi..."

    $env:API_BASE="http://127.0.0.1:8000"          # optional, this is the default
    $env:TEST_CATEGORY="Meals"                     # optional, must be an active category

Exit code is 0 only when classification actually produced a documentType — the failure this script
exists to detect returns null for every field and sets needsManualReview.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import requests

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL")
PASSWORD = os.environ.get("TEST_PASSWORD")
TOKEN = os.environ.get("TEST_TOKEN")
CATEGORY = os.environ.get("TEST_CATEGORY", "Meals")
TIMEOUT = 180  # classification runs synchronously inside POST /claims


def fail(message: str) -> "None":
    print(f"\n!! {message}")
    raise SystemExit(1)


def login() -> str:
    response = requests.post(
        f"{API_BASE}/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        fail(f"login failed [{response.status_code}]: {response.text[:400]}")
    body = response.json()
    if body.get("challenge"):
        fail(f"login returned challenge {body['challenge']} — resolve it in the UI first.")
    token = body.get("accessToken")
    if not token:
        fail(f"login returned no accessToken: {list(body)}")
    user = body.get("user") or {}
    print(f"  logged in as {user.get('email', EMAIL)} (role={user.get('role')})")
    return token


def upload(token: str, path: Path) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    print(f"  uploading {path.name} as {mime} ({path.stat().st_size} bytes)")
    with path.open("rb") as handle:
        response = requests.post(
            f"{API_BASE}/api/expense-items/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (path.name, handle, mime)},
            data={"categoryHint": CATEGORY},
            timeout=TIMEOUT,
        )
    if response.status_code != 200:
        fail(f"upload failed [{response.status_code}]: {response.text[:400]}")
    receipt = response.json()
    print(f"  fileUrl   : {receipt.get('fileUrl')}")
    print(f"  mimeType  : {receipt.get('mimeType')}")
    print(f"  ocrSource : {receipt.get('ocrSource')}")
    if not receipt.get("fileUrl"):
        print("  !! fileUrl is null — S3 storage is off, so no image can be classified.")
    return receipt


def submit(token: str, receipt: dict) -> dict:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    # Duplicate detection keys on vendor + date + amount, so re-running this script with the same
    # receipt would 409 against its own previous run. A per-run vendor suffix keeps it repeatable;
    # classification reads the document itself, so this does not influence what is being tested.
    vendor = f"{receipt.get('suggestedVendor') or 'Test Vendor'} [{uuid.uuid4().hex[:6]}]"
    item = {
        "category": CATEGORY,
        "amount": receipt.get("suggestedAmount") or 42.50,
        "currency": receipt.get("suggestedCurrency") or "USD",
        "amountUSD": receipt.get("suggestedAmount") or 42.50,
        "merchantVendor": vendor,
        "expenseDate": receipt.get("suggestedDate") or yesterday,
        "purposeDescription": "PDF classification end-to-end check.",
        "receiptAttached": True,
        # Echoed straight back from the upload response — this is what ties the claim to the
        # stored object, and fileUrl is the only record of the S3 key.
        "fileUrl": receipt.get("fileUrl"),
        "fileName": receipt.get("fileName"),
        "mimeType": receipt.get("mimeType"),
        "fileSizeBytes": receipt.get("fileSizeBytes"),
        "fileHash": receipt.get("fileHash"),
        "ocrSource": receipt.get("ocrSource"),
        "ocrConfidence": receipt.get("ocrConfidence"),
        "ocrExtractedJson": receipt.get("extraction"),
    }
    response = requests.post(
        f"{API_BASE}/api/claims",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "PDF classification check",
            "currency": item["currency"],
            "items": [item],
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 201:
        fail(f"claim submission failed [{response.status_code}]: {response.text[:600]}")
    return response.json()


def main() -> None:
    if len(sys.argv) < 2:
        fail("usage: python scripts/test_pdf_classification.py <receipt.pdf>")
    path = Path(sys.argv[1])
    if not path.is_file():
        fail(f"no such file: {path}")
    if TOKEN:
        print("1/3 login (skipped — using TEST_TOKEN)")
        token = TOKEN
    elif EMAIL and PASSWORD:
        print("1/3 login")
        token = login()
    else:
        fail("set TEST_TOKEN, or TEST_EMAIL and TEST_PASSWORD, in the environment first.")
    print("2/3 upload receipt")
    receipt = upload(token, path)
    print("3/3 submit claim (classification runs inside this request)")
    claim = submit(token, receipt)

    item = (claim.get("items") or [{}])[0]
    classification = item.get("documentClassification")

    print(f"\nclaim      : {claim.get('claimNumber')}  status={claim.get('status')}")
    print(f"item status: {item.get('status')}")
    print("documentClassification:")
    print(json.dumps(classification, indent=2))

    if not classification:
        fail("no documentClassification at all — the capability is disabled "
             "(check AI_ENABLED and AI_CLASSIFICATION_ENABLED).")
    if classification.get("documentType") is None:
        fail("documentType is null — classification still failed. "
             "Check the server log for ai.classification.image_unusable or provider_failed.")

    print("\nOK: the PDF was classified. "
          f"documentType={classification['documentType']!r} "
          f"suggestedCategory={classification['suggestedCategory']!r} "
          f"confidence={classification['confidence']!r}")


if __name__ == "__main__":
    main()
