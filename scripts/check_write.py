"""
Proves your login may make changes WITHOUT changing anything: it re-sends one
campaign's current status with validate_only=True. Google checks permissions
and validity, then discards the request.

Usage:
  uv run --no-project --with google-ads python scripts/check_write.py <customer_id>
"""

import os
import sys

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

if len(sys.argv) != 2:
    sys.exit(__doc__)
CID = "".join(ch for ch in sys.argv[1] if ch.isdigit())
CREDS = os.environ.get(
    "GOOGLE_ADS_CREDENTIALS", os.path.expanduser("~/.config/google-ads-mcp/google-ads.yaml")
)
client = GoogleAdsClient.load_from_storage(CREDS)
ga = client.get_service("GoogleAdsService")

rows = list(ga.search(customer_id=CID, query=(
    "SELECT campaign.id, campaign.status FROM campaign WHERE campaign.status != 'REMOVED'")))
counts = {}
for r in rows:
    counts[r.campaign.status.name] = counts.get(r.campaign.status.name, 0) + 1
print(f"campaigns: {len(rows)} {counts}")
if not rows:
    sys.exit("no campaigns to probe")

c = rows[0].campaign
op = client.get_type("CampaignOperation")
op.update.resource_name = f"customers/{CID}/campaigns/{c.id}"
op.update.status = c.status  # unchanged value
op.update_mask.paths.append("status")
req = client.get_type("MutateCampaignsRequest")
req.customer_id = CID
req.operations.append(op)
req.validate_only = True  # Google validates and discards; nothing is applied
try:
    client.get_service("CampaignService").mutate_campaigns(request=req)
    print(f"WRITE CHECK PASSED (validate-only, nothing changed) on campaign {c.id}")
except GoogleAdsException as e:
    print("WRITE CHECK FAILED:", "; ".join(f"{x.error_code} {x.message}" for x in e.failure.errors))
