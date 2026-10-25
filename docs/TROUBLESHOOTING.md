# Troubleshooting

Grouped by where you see the problem. "Nothing changed" means exactly that: no change reached your Google Ads account.

## While installing (Windows)

| You see | Why | Fix |
|---|---|---|
| `running scripts is disabled on this system` | PowerShell's execution policy blocks install scripts | Use the commands exactly as in [SETUP.md](SETUP.md): the uv command starts with `powershell -ExecutionPolicy ByPass`. Or install with winget: `winget install --id=astral-sh.uv -e` |
| `uv : The term 'uv' is not recognized` (or `claude`, `git`) | PowerShell was opened before the install, so it doesn't know the new program yet | Close PowerShell, open it again, and run `uv --version`. Still missing? Run the uv install again; it prints the folder it uses (usually `C:\Users\YOU\.local\bin`) |
| `where uv` prints nothing in PowerShell | In PowerShell, `where` means something else | Type `where.exe uv` |
| `claude mcp add` says `unknown option '--from'` | PowerShell didn't pass the `--` on to Claude Code | Write `'--'` (with the quotes) instead of `--` in the command |
| `winget` is not recognized | An older Windows without the App Installer | Use the other install command shown in SETUP.md, or install **App Installer** from the Microsoft Store |

## On Google's sign-in page

| You see | Why | Fix |
|---|---|---|
| **Access blocked: … has not completed the Google verification process** (`access_denied`) | The sign-in app is in **Testing** and your account isn't a test user | Ask your admin to add you under **Test users**, or to make the app internal |
| **Access blocked: … can only be used within its organization** (`org_internal`) | The sign-in app is **Internal**, and your account belongs to another organization | Sign in with your company account |
| **Access blocked: The OAuth client was deleted** (`deleted_client`) | The admin deleted the OAuth client | Ask the admin for a new **Desktop app** client and run `signin.py` again |
| **500. That's an error.** | Usually a browser signed in to several Google accounts, or a setting changed minutes ago | Open the link in a **private window** (see `GADS_NO_BROWSER` in SETUP.md) and try again after a few minutes |
| `signin.py` says "'web' client" | The file is a **Web application** client | The admin must create a **Desktop app** client |

## When Claude or the scripts call Google Ads

| You see | Why | Fix |
|---|---|---|
| `SMOKE FAILED: no builder.toml` or `Builder config not found` | `builder.toml` isn't in `.config\google-ads-mcp` (Windows) or `~/.config/google-ads-mcp` (Mac) | Save it there (SETUP.md step 5), or fix the `GADS_BUILDER_CONFIG` path in the `claude mcp add` command |
| `Your default credentials were not found` | `adc.json` is missing, or the path in `GOOGLE_APPLICATION_CREDENTIALS` is wrong | Run `signin.py` again, and check the path in the `google-ads` server |
| `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION` | The company's Cloud project has no API access yet | The admin applies for an access level (SETUP.md, "For the admin") |
| `the account's daily Google Ads API quota is used up (RESOURCE_EXHAUSTED)` | The team's shared daily limit is used up. The connection is fine | Wait for the time in the message, or until midnight Pacific time. If it came during a real change, look in the account before trying again |
| `larger than the message size limit (RESOURCE_EXHAUSTED)` | Too many or too large images in one request, or a report with too many rows. Not the daily limit | Send less at once: fewer images, fewer campaigns or a shorter date range |
| `USER_PERMISSION_DENIED` | Your Google account can't open that ad account, or you reach it through a manager account | Ask your admin for access to the account |
| `CUSTOMER_NOT_ENABLED` | That ad account is cancelled or was never finished | Ignore it if your real account works |
| `Couldn't reach Google Ads`, `500 Stream removed (RST_STREAM …)` or `UNAVAILABLE` | Your network blocks or cuts connections to Google's APIs. Normal web pages may still load | Use a network that reaches Google APIs. If your network blocks Google APIs, ask your IT team; an ad blocker or DNS filter may also block `googleads.googleapis.com`, so allow it there |
| The check scripts fail only when **Claude** runs them | Claude Code's command sandbox routes traffic through a proxy that this Google connection doesn't work with | Run them yourself in PowerShell or Terminal |

## Inside Claude Code

| You see | Why | Fix |
|---|---|---|
| The tools don't appear, or `/mcp` shows a server as failed | Claude Code loads servers only when it starts, or it can't find `uv` | Restart Claude Code (SETUP.md step 7). Still failed? Put the full path of `uv` (from `where.exe uv` or `which uv`) in place of `uv` and `uvx` |
| Claude made a change **without asking** in Claude Code | The `ask` rules aren't in your Claude Code settings | Add them from [config/claude-settings.json.example](../config/claude-settings.json.example) (SETUP.md step 6). The builder's own window still asked you |
| `Not approved in the approval window (Cancel, closed or timed out). Nothing was changed.` | The window got Cancel, Enter or Esc, was closed, or waited 60 seconds | Nothing changed. Ask again and click **Approve** with the mouse |
| `The approval window couldn't start on this PC: …` (or `on this Mac`) | No window could open: Windows PowerShell is missing or locked down, it couldn't compile the window's click check (`Add-Type`), or macOS couldn't show the dialog. The text after the colon says which | Nothing changed, and it doesn't count as a Cancel. See the next rows |
| **Windows:** no approval window appears | It may be behind a full-screen app, or Windows PowerShell can't show windows on this PC | Look for a flashing icon in the taskbar. Then test windows in PowerShell: `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('test')`. If no box appears, ask your IT team to allow Windows PowerShell and Windows Forms for your account |
| **Windows:** the window never opens and the test above fails with a "language mode" error, or the message says `Cannot add type` or `language mode` | Your company locks PowerShell (Constrained Language Mode, AppLocker or Device Guard), so it can't compile the window's click check | Ask your IT team to allow `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` with Windows Forms and `Add-Type` for your account. `$ExecutionContext.SessionState.LanguageMode` must print `FullLanguage` |
| **Windows:** **Approve** is greyed out | It stays off for 1.5 seconds after the window appears or moves, so a window that pops up under the pointer can't catch a click meant for something else | Wait a moment, then click it |
| **Windows:** clicking **Approve** does nothing | The window counts only a real click with your mouse or touchpad, made while the window stays put. Clicks that a program makes (remote-control, macro or button-remapping tools) are refused, and a touch screen's or pen's taps may not count either | Click with the mouse or touchpad on the PC itself, with remapping tools paused. Nothing is approved until you do; Cancel or the timeout still work |
| **Mac:** no approval window appears | macOS couldn't show the dialog | Run `osascript -e 'display dialog "test"'` in Terminal to check that dialogs work |
| `The builder's approval window opens only on macOS and Windows` | Claude Code runs on Linux, or inside WSL on Windows, where the window can't open | Run Claude Code on Windows itself (PowerShell or VS Code), not in WSL |
| Arabic or Persian text looks garbled (`Ø§Ù„…`) or `UnicodeEncodeError` | Python on Windows used an old code page instead of UTF-8 | Add `-e PYTHONUTF8=1` to both `claude mcp add` commands (or `"PYTHONUTF8": "1"` in `.mcp.json`) and restart Claude Code. For scripts you run yourself, first run `$env:PYTHONUTF8 = "1"` |
| `This plan was declined 3 times; draft a new plan` | Three answers other than **Approve** for the same plan. They are counted from the plan's `approvals.jsonl`, so checking again or restarting doesn't reset them | Nothing changed. Tell Claude what you want instead; it drafts a new plan |
| `Another approval window is open; answer it first`, or `Another approval window is already open on this computer` | A change tool was called while another approval window was open, in this Claude Code session or another one. Only one window opens at a time | Nothing changed, and it doesn't count as a Cancel. Answer or close the open window first |
| `too many items to show in one approval window; split the plan` | The window fits 45 lines; a longer list is refused, never cut | Ask Claude to split the plan into smaller plans |
| `Codex isn't installed; install it or set codex_path in builder.toml` | `generate_image` can't find Codex | Give Claude your own image files, or install Codex and restart Claude Code. On a Mac the builder uses the ChatGPT app's Codex |
| `On Windows, codex_path must be the full path of codex.exe` | `codex_path` in `builder.toml` names a `.cmd` or `.bat` file (such as npm's `codex.cmd`), another kind of file, or a path that doesn't start with a drive letter | Remove `codex_path` (the builder finds npm's `codex.exe` itself) or set it to the full path of `codex.exe`. See [SETUP.md](SETUP.md#optional-images-with-codex) |
| **Windows:** `Codex isn't installed` although `codex` works in PowerShell | PowerShell runs npm's `codex.cmd`. The builder runs only `codex.exe`: on `PATH`, or where npm installs it, never one in the folder Claude Code was started in | Check where `codex.exe` is with the command in [SETUP.md](SETUP.md#optional-images-with-codex), and put its full path in `codex_path` if it is elsewhere |
| A `check_landing_pages` result has the note `resolved through the VPN's fake-IP DNS (198.18.x.x)` | A VPN app in fake-IP mode answers DNS with addresses in 198.18.0.0/15. The builder allows that range only, and still opens only your allowed domains | Nothing to fix. The load time includes the VPN's detour |
| `check_landing_pages`: `not opened: <host> resolves to <address>, which is not a public internet address` | The host resolved to a private or local address: a VPN that uses another range, or a page on your own network | Switch the VPN app's DNS to real addresses while checking, then try again |
