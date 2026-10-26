r"""
Makes your builder.toml from your own Google Ads account, after scripts/signin.py. It only reads the account.

Usage (Mac Terminal or Windows PowerShell, in the google-ads-builder folder):
  uv run --no-project --with google-ads python scripts/make_settings.py

It lists the ad accounts your login can reach (also those under a manager account), asks which one to use, and
suggests the rest from that account: its currency, its country, the apps its App campaigns promote, the websites
its ads link to and a daily budget cap. Press Enter to accept a suggestion, or type your own. Then it writes
~/.config/google-ads-mcp/builder.toml (Windows: %USERPROFILE%\.config\google-ads-mcp\builder.toml).

Options, to answer without questions:
  --customer-id 1234567890   the ad account
  --cap 500                  the daily budget cap, in the account's currency
  --country US               two-letter country for location lookups
  --domains a.com,b.com      the websites Search ads may link to
  --yes                      accept every suggestion
  --out <path>               write somewhere else
"""

import argparse
import json
import logging
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

HOME_CONFIG = Path.home() / ".config" / "google-ads-mcp"
STORES = {"GOOGLE_APP_STORE", "APPLE_APP_STORE"}
# Sites many advertisers share (stores, forms, social, link shorteners): never suggested as "your" website, since
# allowing one would let ads link to anyone's pages on it. Add one by hand only if you mean to.
SHARED = ("play.google.com", "apps.apple.com", "itunes.apple.com", "docs.google.com", "forms.gle", "sites.google.com",
          "drive.google.com", "goo.gl", "g.page", "maps.google.com", "youtube.com", "youtu.be", "facebook.com",
          "fb.me", "instagram.com", "wa.me", "whatsapp.com", "t.me", "telegram.me", "tiktok.com", "x.com",
          "twitter.com", "linkedin.com", "linktr.ee", "bit.ly", "typeform.com", "forms.office.com", "app.adjust.com",
          "adj.st", "onelink.me", "app.link", "page.link")


def toml_string(value: str) -> str:
    """A TOML basic string (JSON's escapes are valid TOML)."""
    return json.dumps(value, ensure_ascii=False)


def toml_list(values) -> str:
    return "[" + ", ".join(toml_string(v) for v in values) + "]"


def render(s: dict) -> str:
    """The builder.toml text for the chosen settings."""
    cap = s["daily_budget_cap"]
    lines = [
        "# The builder's settings, made by scripts/make_settings.py. Edit them any time; restart Claude Code after.",
        "# On Windows, write paths with forward slashes, such as \"C:/Users/YOU/Pictures/ads\".",
        f"customer_id = {toml_string(s['customer_id'])}        # {s.get('account_name', '')}".rstrip(),
        f"currency = {toml_string(s['currency'])}",
        f"daily_budget_cap = {int(cap) if float(cap).is_integer() else cap}"
        "             # a budget above this needs the exact amount confirmed",
        f"country_code = {toml_string(s['country_code'])}                 # two-letter country for location lookups",
        'plans_dir = "~/Documents/Google Ads/Plans"',
        'insights_dir = "~/Documents/Google Ads/Insights"',
        'images_dir = "~/Documents/Google Ads/Images"',
        "# blocked_words = [\"CompetitorName\"]  # words that must never appear in ad text",
        f"domains = {toml_list(s['domains'])}"
        "  # Search final URLs and sitelinks: https on these hosts or their subdomains",
        "# brand_words = [\"YourBrand\"]  # keywords with these words are brand keywords (kept in their own campaign)",
    ]
    if not s["apps"]:
        lines += ["", "# No App campaign found in the account: this placeholder allows no real app. To build App",
                  "# campaigns, put your app here (Android: its package name; iPhone: its App Store number)."]
    for store, app_id in s["apps"] or [("GOOGLE_APP_STORE", "com.example.app")]:
        lines += ["", "[[apps]]                            # an app campaigns may promote",
                  f"store = {toml_string(store)}", f"id = {toml_string(app_id)}"]
    return "\n".join(lines) + "\n"


def host_of(url: str) -> str | None:
    """The host of an https ad link, without "www.", or None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    return host[4:] if host.startswith("www.") else host


def is_shared(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in SHARED)


def domains_from(urls, keep_shared: bool = True) -> list[str]:
    """The distinct hosts of the ad links, a parent first, its subdomains left out (the builder allows them)."""
    hosts = sorted({h for h in map(host_of, urls) if h and (keep_shared or not is_shared(h))},
                   key=lambda h: (h.count("."), h))
    kept: list[str] = []
    for h in hosts:
        if not any(h == k or h.endswith("." + k) for k in kept):
            kept.append(h)
    return sorted(kept)


def country_for_zone(zone: str) -> str | None:
    """The two-letter country of a time zone such as Europe/Berlin, from the system's zone table if it has one."""
    candidates = [Path("/usr/share/zoneinfo/zone1970.tab"), Path("/usr/share/zoneinfo/zone.tab")]
    try:
        import tzdata  # noqa: F401  (on Windows the table comes with the tzdata package, when it is installed)
        candidates.append(Path(tzdata.__file__).parent / "zoneinfo" / "zone1970.tab")
    except ImportError:
        pass
    for table in candidates:
        try:
            for line in table.read_text(encoding="utf-8").splitlines():
                cols = line.split("\t")
                if not line.startswith("#") and len(cols) >= 3 and cols[2] == zone:
                    return cols[0].split(",")[0].upper()
        except OSError:
            continue
    return None


def suggested_cap(budgets_micros) -> float:
    """The largest current daily budget, rounded up to a round number; 100 when there is none."""
    top = max((b / 1_000_000 for b in budgets_micros), default=0)
    if top <= 0:
        return 100.0
    step = 10 ** max(0, int(math.log10(top)) - 1)
    return float(math.ceil(top / step) * step)


def ask(question: str, suggestion: str, assume_yes: bool) -> str:
    if assume_yes:
        print(f"{question} {suggestion}")
        return suggestion
    answer = input(f"{question} [{suggestion}] ").strip()
    return answer or suggestion


def set_login_customer_id(yaml_path: Path, manager_id: str | None) -> None:
    """Writes (or removes) login_customer_id in google-ads.yaml: needed when the ad account is reached through a
    manager account."""
    lines = [l for l in yaml_path.read_text(encoding="utf-8").splitlines() if not l.startswith("login_customer_id:")]
    if manager_id:
        lines.append(f"login_customer_id: {manager_id}")
    fd = os.open(yaml_path, os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, 0o600)


def main() -> None:
    p = argparse.ArgumentParser(description="Make builder.toml from your own Google Ads account (read-only).")
    p.add_argument("--customer-id")
    p.add_argument("--cap", type=float)
    p.add_argument("--country")
    p.add_argument("--domains")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--out", type=Path, default=HOME_CONFIG / "builder.toml")
    a = p.parse_args()

    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException

    logging.getLogger("google.ads.googleads").setLevel(logging.CRITICAL)  # no line per closed account

    creds = Path(os.environ.get("GOOGLE_ADS_CREDENTIALS", HOME_CONFIG / "google-ads.yaml"))
    if not creds.is_file():
        sys.exit(f"No login at {creds}: run scripts/signin.py first (docs/SETUP.md, step 4).")
    client = GoogleAdsClient.load_from_storage(str(creds))

    def errs(e):
        return "; ".join(f"{x.error_code} {x.message}".replace("\n", " ") for x in e.failure.errors)

    def rows(cid, query, login=None):
        """Read-only search; through the manager account `login` when given (the header is set per service)."""
        client.login_customer_id = login
        return list(client.get_service("GoogleAdsService").search(customer_id=cid, query=query))

    try:
        client.login_customer_id = None  # list from your own login, whatever google-ads.yaml says
        top = [r.split("/")[1] for r in
               client.get_service("CustomerService").list_accessible_customers().resource_names]
    except GoogleAdsException as e:
        sys.exit(f"Google refused to list your accounts: {errs(e)} (see docs/TROUBLESHOOTING.md)")

    accounts = []  # (id, name, currency, time zone, manager id or None)
    for cid in top:
        try:
            c = rows(cid, "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
                          "customer.time_zone, customer.manager, customer.status FROM customer LIMIT 1")[0].customer
        except (GoogleAdsException, IndexError):
            continue
        if c.status.name != "ENABLED":
            continue
        if not c.manager:
            accounts.append((str(c.id), c.descriptive_name, c.currency_code, c.time_zone, None))
            continue
        try:
            for r in rows(cid, "SELECT customer_client.id, customer_client.descriptive_name, "
                               "customer_client.currency_code, customer_client.time_zone, customer_client.status "
                               "FROM customer_client WHERE customer_client.manager = FALSE "
                               "AND customer_client.status = 'ENABLED'", login=cid):
                cc = r.customer_client
                accounts.append((str(cc.id), cc.descriptive_name, cc.currency_code, cc.time_zone, cid))
        except GoogleAdsException:
            continue
    seen, unique = set(), []
    for acc in accounts:  # an account both direct and under a manager: keep the direct one
        if acc[0] not in seen:
            seen.add(acc[0])
            unique.append(acc)
    accounts = sorted(unique, key=lambda x: (x[4] is not None, x[1].lower()))
    if not accounts:
        sys.exit("Your Google account can't open any active ad account. Ask the account's owner to add you "
                 "(Standard access is enough), then run this again.")

    if a.customer_id:
        want = re.sub(r"\D", "", a.customer_id)
        chosen = next((x for x in accounts if x[0] == want), None)
        if not chosen:
            sys.exit(f"{a.customer_id} isn't among the accounts your login can open.")
    elif len(accounts) == 1:
        chosen = accounts[0]
    else:
        print("Ad accounts your login can open:")
        for i, (cid, name, cur, _, mgr) in enumerate(accounts, 1):
            print(f"  {i}. {name} ({cid[:3]}-{cid[3:6]}-{cid[6:]}, {cur})" + (f", via manager {mgr}" if mgr else ""))
        while True:
            pick = input("Which one? Type its number: ").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(accounts):
                chosen = accounts[int(pick) - 1]
                break
    cid, name, currency, zone, manager = chosen
    print(f"Account: {name} ({cid}), {currency}" + (f", through manager account {manager}" if manager else ""))

    login = manager
    apps = sorted({(r.campaign.app_campaign_setting.app_store.name, r.campaign.app_campaign_setting.app_id)
                   for r in rows(cid, "SELECT campaign.app_campaign_setting.app_id, "
                                      "campaign.app_campaign_setting.app_store FROM campaign "
                                      "WHERE campaign.advertising_channel_type = 'MULTI_CHANNEL' "
                                      "AND campaign.status != 'REMOVED'", login=login)
                   if r.campaign.app_campaign_setting.app_id
                   and r.campaign.app_campaign_setting.app_store.name in STORES})
    urls = [u for r in rows(cid, "SELECT ad_group_ad.ad.final_urls FROM ad_group_ad "
                                 "WHERE ad_group_ad.status != 'REMOVED' LIMIT 2000", login=login)
            for u in r.ad_group_ad.ad.final_urls]
    budgets = [r.campaign_budget.amount_micros for r in rows(
        cid, "SELECT campaign_budget.amount_micros FROM campaign WHERE campaign.status = 'ENABLED'", login=login)]

    try:
        cap = a.cap if a.cap else float(ask(f"Daily budget cap in {currency} (a new campaign's budget above it "
                                            "needs the exact amount typed):", f"{suggested_cap(budgets):g}", a.yes))
    except ValueError:
        sys.exit("The cap must be a number, such as 500.")
    if not math.isfinite(cap) or cap <= 0:
        sys.exit("The cap must be a positive number.")
    country = (a.country or ask("Two-letter country for location lookups:", country_for_zone(zone) or "US",
                                a.yes)).upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        sys.exit("The country must be two letters, such as US or DE.")
    found = ",".join(domains_from(urls, keep_shared=False))
    shared = sorted({h for h in map(host_of, urls) if h and is_shared(h)})
    if shared:
        print("Left out of the websites below, because other advertisers use them too: " + ", ".join(shared))
    raw = a.domains if a.domains is not None else ask(
        "Websites your Search ads may link to, separated by commas (Enter for none):", found, a.yes)
    domains = domains_from(f"https://{d.strip()}" for d in raw.split(",") if d.strip())
    print("Apps your App campaigns may promote: " + (", ".join(f"{i} ({s})" for s, i in apps) or
                                                     "none found (a placeholder is written; edit it later)"))

    text = render({"customer_id": f"{cid[:3]}-{cid[3:6]}-{cid[6:]}", "account_name": name, "currency": currency,
                   "daily_budget_cap": cap, "country_code": country, "domains": domains, "apps": apps})
    out = a.out.expanduser()
    if out.exists():
        backup = out.with_name(f"{out.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        out.replace(backup)
        print(f"Your old settings are kept in {backup}")
    write_private(out, text)
    set_login_customer_id(creds, manager)
    print(f"Saved {out}")
    if manager:
        print(f"Your login now goes through manager account {manager} (login_customer_id in {creds.name}).")
    print("Next: check it with `uv run --frozen python scripts/smoke.py` in the builder folder (docs/SETUP.md).")


if __name__ == "__main__":
    main()
