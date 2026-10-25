"""
Read-only check: which Google Ads accounts your login can reach, with name,
currency and status. Changes nothing.

Usage:
  uv run --no-project --with google-ads python scripts/check_access.py
"""

import os

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

CREDS = os.environ.get(
    "GOOGLE_ADS_CREDENTIALS", os.path.expanduser("~/.config/google-ads-mcp/google-ads.yaml")
)
client = GoogleAdsClient.load_from_storage(CREDS)


def errs(e):
    return "; ".join(f"{x.error_code} {x.message}".replace("\n", " ") for x in e.failure.errors)


try:
    ids = [
        r.split("/")[1]
        for r in client.get_service("CustomerService").list_accessible_customers().resource_names
    ]
except GoogleAdsException as e:
    raise SystemExit(f"list_accessible_customers FAILED: {errs(e)}")
print(f"accessible accounts: {len(ids)}")

ga = client.get_service("GoogleAdsService")
for cid in ids:
    try:
        row = next(iter(ga.search(customer_id=cid, query=(
            "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
            "customer.manager, customer.status FROM customer LIMIT 1"))))
        c = row.customer
        print(f"  {c.id} | {c.descriptive_name} | {c.currency_code} | "
              f"manager={c.manager} | {c.status.name}")
    except GoogleAdsException as e:
        print(f"  {cid} | ERROR {errs(e)}")
