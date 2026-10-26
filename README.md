# Google Ads campaign builder for Claude Code

Ask Claude to build complete Google Ads campaigns from your account's own history. **The builder changes nothing in your account until you click Approve**, and every new campaign starts **paused**. [SECURITY.md](SECURITY.md) says what that protects against and what it doesn't.

It runs on your own computer, **Windows or Mac**. It is two servers that Claude Code starts for you:

| Server | What it is | Can it change the account? |
|---|---|---|
| `google-ads` | [Google's official Google Ads MCP server](https://github.com/googleads/google-ads-mcp). Answers questions about your account. | No. Read-only. |
| `google-ads-builder` | This repo (folder [`builder/`](builder/)). Plans App, Search, Demand Gen and Performance Max campaigns. | Only after you click **Approve** in its own window. |

**You need:** a Windows 10 or 11 PC or a Mac, [Claude Code](https://claude.com/claude-code) (in the terminal or in VS Code), `uv`, Git (or the ZIP download), and a Google account that can open your Google Ads account.

**Set it up:** follow [docs/SETUP.md](docs/SETUP.md). It has exact steps for Windows (PowerShell) and for Mac. You make your own Google sign-in file (in your own free Google Cloud project) and your own settings with `scripts/make_settings.py`: nobody needs to send you a file, and no developer token is needed.

- [SECURITY.md](SECURITY.md): how changes are kept safe, and the risks that remain.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): errors and their fixes.

---

## What you can ask

- *"Make a Search campaign for gym memberships like <search campaign>, 80 a day."*
- *"Launch city B like <app campaign>, 300 a day."*
- *"Which search terms waste money in <search campaign>? Suggest negative keywords."*
- *"Replace the weakest headlines in <search campaign>'s ads."*
- *"Which of our YouTube videos work best?"*
- *"Make a Performance Max campaign for online sign-ups like <campaign>, 50 a day."*

## The safety flow

Every change goes through the same four steps. You can't skip one.

1. **Plan.** Claude saves a plan and shows you a summary: every setting, keyword, text, image and the budget.
2. **Google checks it.** Google validates the whole plan in **check-only** mode. Nothing is created.
3. **You approve it.** The builder opens **its own window** on your computer with the account, campaign, budget and every item. Only a click on **Approve** with the mouse sends the change. Cancel, Enter, Esc, closing the window or waiting 60 seconds means no.
4. **Created paused.** New campaigns are always created **PAUSED**. You switch them on yourself in Google Ads. **No tool here can switch a campaign on.**

One exception to know: an asset update (new or removed headlines, images or videos) on a campaign that is already running takes effect at once. The window says so before you approve.

## The builder's 15 tools

| Tool | What it does | Changes the account? |
|---|---|---|
| `playbook` | The builder's workflow and rules. Claude reads it before any campaign work. | No |
| `list_campaigns` | Lists campaigns of one type, with goal, budget and target cost. | No |
| `describe_campaign` | Settings, targeting and ads of one campaign, plus a ready-to-edit plan copied from it. | No |
| `find_locations` | Google's location IDs for city or region names. | No |
| `creative_insights` | The best and worst headlines, descriptions, images and videos of similar campaigns. Saves a report and the images on your computer. | No |
| `search_insights` | For Search campaigns: keyword Quality Scores, ad text results, and search terms to add or exclude. Saves a report. | No |
| `video_insights` | Ranks your YouTube videos across video, App and Demand Gen campaigns. Saves a report. | No |
| `check_landing_pages` | Opens your own pages (allowed domains only) and checks status, speed, mobile view, title and keywords. | No |
| `generate_image` | Makes one ad image with Codex (your ChatGPT Plus allowance) and sizes it for Google Ads. | No |
| `draft_plan` | Saves a plan, checks it against the local rules and returns the summary. | No |
| `check_plan` | Google validates the whole plan in check-only mode. | No |
| `quality_report` | After creation, reads the campaign back and lists what to review. | No |
| `create_campaign_from_plan` | Creates the whole campaign in one all-or-nothing request, always **paused**. | **Yes, after you approve** |
| `upload_images` | Uploads a plan's images to the account's asset library. | **Yes, after you approve** |
| `update_campaign_assets` | Adds or removes texts, images and videos on one existing ad. | **Yes, after you approve** |

`generate_image` needs Codex. On a Mac the builder uses the one inside the ChatGPT app; on Windows it uses `codex.exe` if it is installed (see [SETUP.md](docs/SETUP.md#optional-images-with-codex)). Without it, Claude asks you for image files instead.

## For developers

```
cd builder
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen python scripts/smoke.py
```

The tests run offline: a guard blocks all network access, and another stops any test from opening the approval window. GitHub Actions runs them on Windows, macOS and Linux on every push. The live tests (`tests/test_live.py`) use Google's check-only mode on a real account and are skipped unless you set `GADS_LIVE=1`; the file's docstring explains them.
