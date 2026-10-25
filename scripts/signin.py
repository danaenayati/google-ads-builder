r"""
One-time Google sign-in for the builder and Google's official Google Ads MCP server.

Usage (Mac Terminal or Windows PowerShell, in the google-ads-builder folder):
  uv run --no-project --with google-auth-oauthlib python scripts/signin.py <oauth-client.json>

<oauth-client.json> is the "Desktop app" OAuth client you downloaded from
Google Cloud (Google Auth Platform -> Clients). A "Web application" client
will not work.

Writes to ~/.config/google-ads-mcp/ (on Windows %USERPROFILE%\.config\google-ads-mcp\).
On a Mac the folder is 700 and the files 600; on Windows the folder inside your user profile is
already private to your Windows account.
  google-ads.yaml  -> the builder (google-ads-builder), via `credentials` in builder.toml
  adc.json         -> Google's official read-only server (google-ads), via GOOGLE_APPLICATION_CREDENTIALS

Optional environment variables (in PowerShell: $env:GADS_LOGIN_HINT = "you@company.com"):
  GADS_LOGIN_HINT=you@company.com   preselect the account on Google's page
  GADS_NO_BROWSER=1                 only print the link (open it in a private window)
  GADS_CREDENTIALS_DIR=<path>       write somewhere other than ~/.config/google-ads-mcp
  GOOGLE_ADS_DEVELOPER_TOKEN=<tok>  only if your setup still needs one; Google
                                    stopped requiring it on 2026-09-09
"""

import json
import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/adwords"]
OUT = Path(os.environ.get("GADS_CREDENTIALS_DIR", Path.home() / ".config" / "google-ads-mcp"))


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, 0o600)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    client_file = Path(sys.argv[1]).expanduser()
    kind = next(iter(json.loads(client_file.read_text(encoding="utf-8-sig"))))
    if kind != "installed":
        sys.exit(f"{client_file.name} is a '{kind}' client. Create a 'Desktop app' client instead.")

    hint = os.environ.get("GADS_LOGIN_HINT")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=not os.environ.get("GADS_NO_BROWSER"),
        access_type="offline",
        prompt="consent",  # guarantees a refresh token even on re-consent
        authorization_prompt_message="If the browser did not open, visit:\n{url}\n",
        success_message="Signed in. You can close this tab.",
        **({"login_hint": hint} if hint else {}),
    )
    if not creds.refresh_token:
        sys.exit("Google returned no refresh token; run again.")

    OUT.mkdir(parents=True, exist_ok=True)
    os.chmod(OUT, 0o700)
    dev_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
    write_private(
        OUT / "google-ads.yaml",
        (f"developer_token: {dev_token}\n" if dev_token else "")
        + f"client_id: {creds.client_id}\n"
        f"client_secret: {creds.client_secret}\n"
        f"refresh_token: {creds.refresh_token}\n"
        "use_proto_plus: True\n",
    )
    write_private(
        OUT / "adc.json",
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "refresh_token": creds.refresh_token,
            },
            indent=2,
        ),
    )
    print(f"Saved credentials to {OUT}")


if __name__ == "__main__":
    main()
