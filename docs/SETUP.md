# Setup

About 30 minutes. Pick your computer: **[Windows](#windows)** or **[Mac](#mac)**. The admin who sets this up for a company once also reads **[For the admin](#for-the-admin-once-per-company)** at the end.

**Before you start, get two files from your admin** (on our team, Dana). Get them privately: in person, in a password manager or in a private chat. **Never through GitHub**, and never in a shared channel.

1. The Google sign-in file, a "Desktop app" OAuth client. Its name looks like `client_secret_XXXX.json`.
2. Your `builder.toml`, the builder's settings for your company's ad account.

Your Google account also needs access to the Google Ads account. Ask your admin if you can't open it in Google Ads.

In the commands below, `YOU` stands for your user name on the computer. You can copy each command as it is: `$HOME` (Windows) and `~` or `$HOME` (Mac) mean your own user folder.

---

## Windows

### How to open PowerShell

1. Press the **Windows key**.
2. Type `PowerShell`.
3. Click **Windows PowerShell**.

A window with a prompt like `PS C:\Users\YOU>` opens. Paste a command with **Ctrl+V** or a right-click, then press **Enter**.

### 1. Install Git, uv and Claude Code

1. Install **Git** (Claude Code on Windows uses it too):

   ```powershell
   winget install --id Git.Git -e
   ```

   No `winget`? Download Git from [git-scm.com/download/win](https://git-scm.com/download/win) and install it with the default options.

2. Install **uv**, which runs the builder:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Or, with winget:

   ```powershell
   winget install --id=astral-sh.uv -e
   ```

3. Install **Claude Code**:

   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```

   You can also use Claude Code inside **VS Code**: install [VS Code](https://code.visualstudio.com), open **Extensions** (Ctrl+Shift+X), search for **Claude Code** by Anthropic and click **Install**. You still need the steps below, because they set up the servers for both.

4. **Close PowerShell and open it again**, so it finds the new programs. Then check them. Each one prints a version number:

   ```powershell
   git --version
   uv --version
   claude --version
   ```

5. Run `claude` once and sign in to your Claude account. Then type `/exit` to leave it.

### 2. Get the builder

```powershell
cd $HOME
git clone https://github.com/danaenayati/google-ads-builder.git
```

This makes the folder `C:\Users\YOU\google-ads-builder`.

**Without Git:** open [github.com/danaenayati/google-ads-builder](https://github.com/danaenayati/google-ads-builder), click **Code → Download ZIP**, and extract it into `C:\Users\YOU\`. Rename the folder `google-ads-builder-main` to `google-ads-builder`.

### 3. Save the sign-in file

Save the `client_secret_XXXX.json` file from your admin in your **Downloads** folder.

### 4. Sign in to Google once

```powershell
cd $HOME\google-ads-builder
uv run --no-project --with google-auth-oauthlib python scripts/signin.py "$HOME\Downloads\client_secret_XXXX.json"
```

Replace `client_secret_XXXX.json` with the real file name. Your browser opens:

1. Sign in with **the Google account that has Google Ads access**.
2. Click **Allow**.
3. PowerShell prints `Saved credentials to C:\Users\YOU\.config\google-ads-mcp`.

The folder `%USERPROFILE%\.config\google-ads-mcp\` now holds your login (`google-ads.yaml` and `adc.json`). It sits in your user folder, which Windows keeps private to your account. Never share these files or put them in the repo.

Signed in to several Google accounts in your browser? Run `$env:GADS_NO_BROWSER = "1"` first, run the command again, and open the link it prints in a private window.

### 5. Save builder.toml

Save the `builder.toml` from your admin in your Downloads folder, then copy it next to your login:

```powershell
Copy-Item "$HOME\Downloads\builder.toml" "$HOME\.config\google-ads-mcp\builder.toml"
```

Check that the builder starts with it. The first run downloads Python and the builder's packages, which takes a few minutes:

```powershell
cd $HOME\google-ads-builder\builder
uv run --frozen python scripts/smoke.py
```

The last line must be `SMOKE OK`.

### 6. Add both servers to Claude Code

1. Run these two commands. Each is one line:

   ```powershell
   claude mcp add google-ads -s user -e "GOOGLE_APPLICATION_CREDENTIALS=$HOME\.config\google-ads-mcp\adc.json" -e PYTHONUTF8=1 -- uvx --from git+https://github.com/googleads/google-ads-mcp.git google-ads-mcp
   ```

   ```powershell
   claude mcp add google-ads-builder -s user -e "GADS_BUILDER_CONFIG=$HOME\.config\google-ads-mcp\builder.toml" -e PYTHONUTF8=1 -- uv run --frozen --project "$HOME\google-ads-builder\builder" gads-builder
   ```

   `-s user` makes both servers work in every folder you open with Claude Code. `PYTHONUTF8=1` makes Arabic, Persian and other non-English text work.

2. Add the safety settings. They make Claude Code ask you before each of the builder's three change tools, and stop Claude from reading your login files:

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.claude" | Out-Null
   if (Test-Path "$HOME\.claude\settings.json") { notepad "$HOME\.claude\settings.json" } else { Copy-Item "$HOME\google-ads-builder\config\claude-settings.json.example" "$HOME\.claude\settings.json" }
   ```

   If Notepad opens, you already have settings. Add the three `ask` lines and the `deny` line from [`config/claude-settings.json.example`](../config/claude-settings.json.example) into its `"permissions"` section, then save.

**The other way: a `.mcp.json` file.** Instead of the two `claude mcp add` commands, you can copy what is inside `windows` → `mcpServers` in [`config/mcp.json.example`](../config/mcp.json.example) into a file named `.mcp.json` in the folder you open in Claude Code. Replace `YOU` with your user name. Claude Code asks once whether to use the servers in it.

If Claude Code can't find `uv` or `uvx`, use their full path. This prints it:

```powershell
where.exe uv
```

### 7. Restart Claude Code

Claude Code loads servers only when it starts.

- **In PowerShell:** type `/exit`, then run `claude` again.
- **In VS Code:** press **Ctrl+Shift+P**, type `Reload Window` and press **Enter**.

Then type `/mcp` in Claude Code. Both `google-ads` and `google-ads-builder` must show as connected.

### 8. First check: read only

Ask Claude:

> *"Use list_campaigns from google-ads-builder to list our Search campaigns."*

Claude lists the campaigns. Nothing changes.

### 9. Test the approval window, and click Cancel

This proves that the window appears on your computer. Nothing is created.

1. Ask Claude:

   > *"Copy the Search campaign <name> into a test plan called TEST - do not create, with a budget of 1 a day. Draft it, check it, then call create_campaign_from_plan. I will click Cancel."*

2. Claude shows the plan's summary, and Google checks it in check-only mode.
3. Claude Code asks whether to run `create_campaign_from_plan`. Choose **Yes**: the builder's own window is the real gate.
4. A window titled **Google Ads: approve this change?** opens on top of your other windows. It lists the account, the campaign and the budget.
5. Click **Cancel**.
6. Claude answers `Not approved in the approval window (Cancel, closed or timed out). Nothing was changed.`

If no window appears, see [Troubleshooting](TROUBLESHOOTING.md).

**Try the window on its own (optional).** This opens the same window without Claude and without Google, so nothing can be sent. It prints `True` only when you click **Approve**; Cancel, Enter, Esc, closing it or waiting 60 seconds print `False`:

```powershell
cd $HOME\google-ads-builder\builder
uv run --frozen python -c "from gads_builder import confirm; print(confirm.ask_person('Test window', ['Nothing is sent. Try Enter, Esc, Cancel and Approve.', 'Arabic: مرحبا  Persian: می‌خواهم']))"
```

On a Mac, run `cd ~/google-ads-builder/builder` first, then the same `uv run` line.

### 10. The daily API limit

The whole team shares **one Google Cloud project**, and Google gives that project a daily limit of API requests. Big reports and checks use more of it.

- If it runs out, Claude shows: `the account's daily Google Ads API quota is used up (RESOURCE_EXHAUSTED)`. Nothing is broken.
- The limit resets at **midnight Pacific time** (Google's time zone for the quota).
- If the team runs out often, the admin can apply for a higher access level, such as **Basic**. See Google's [access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).

---

## Mac

### How to open Terminal

Press **Cmd+Space**, type `Terminal` and press **Enter**. Paste a command with **Cmd+V**, then press **Enter**.

### 1. Install Git, uv and Claude Code

1. **Git:** type `git --version`. If macOS offers to install the command line developer tools, click **Install**. Or, with [Homebrew](https://brew.sh): `brew install git`.
2. **uv:**

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Or, with Homebrew: `brew install uv`.

3. **Claude Code:**

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```

   Or, with Homebrew: `brew install --cask claude-code`. You can also use Claude Code inside **VS Code**: open **Extensions** (Cmd+Shift+X), search for **Claude Code** by Anthropic and click **Install**.

4. **Close Terminal and open it again.** Check that each prints a version number:

   ```bash
   git --version
   uv --version
   claude --version
   ```

5. Run `claude` once and sign in to your Claude account. Then type `/exit`.

### 2. Get the builder

```bash
cd ~
git clone https://github.com/danaenayati/google-ads-builder.git
```

This makes the folder `/Users/YOU/google-ads-builder`. Without Git, download the ZIP as described for Windows, extract it into your home folder and rename it to `google-ads-builder`.

### 3. Save the sign-in file

Save `client_secret_XXXX.json` from your admin in your **Downloads** folder.

### 4. Sign in to Google once

```bash
cd ~/google-ads-builder
uv run --no-project --with google-auth-oauthlib python scripts/signin.py ~/Downloads/client_secret_XXXX.json
```

Sign in with the Google account that has Google Ads access, and click **Allow**. The script saves your login in `~/.config/google-ads-mcp/` (a folder only your Mac user can open). Signed in to several Google accounts? Put `GADS_NO_BROWSER=1 ` in front of the command and open the printed link in a private window.

### 5. Save builder.toml

```bash
cp ~/Downloads/builder.toml ~/.config/google-ads-mcp/builder.toml
chmod 600 ~/.config/google-ads-mcp/builder.toml
cd ~/google-ads-builder/builder
uv run --frozen python scripts/smoke.py
```

The last line must be `SMOKE OK`.

### 6. Add both servers to Claude Code

1. Run these two commands. Each is one line:

   ```bash
   claude mcp add google-ads -s user -e GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/google-ads-mcp/adc.json" -- uvx --from git+https://github.com/googleads/google-ads-mcp.git google-ads-mcp
   ```

   ```bash
   claude mcp add google-ads-builder -s user -e GADS_BUILDER_CONFIG="$HOME/.config/google-ads-mcp/builder.toml" -- uv run --frozen --project "$HOME/google-ads-builder/builder" gads-builder
   ```

2. Add the safety settings:

   ```bash
   mkdir -p ~/.claude
   [ -f ~/.claude/settings.json ] && open -e ~/.claude/settings.json || cp ~/google-ads-builder/config/claude-settings.json.example ~/.claude/settings.json
   ```

   If TextEdit opens, you already have settings. Add the three `ask` lines and the `deny` line from [`config/claude-settings.json.example`](../config/claude-settings.json.example) into its `"permissions"` section, then save.

**The other way:** copy what is inside `mac` → `mcpServers` in [`config/mcp.json.example`](../config/mcp.json.example) into a `.mcp.json` file in the folder you open in Claude Code, and replace `YOU` with your user name. If Claude Code can't find `uv` or `uvx`, use the full path that `which uv` prints.

### 7. Restart Claude Code

- **In Terminal:** type `/exit`, then run `claude` again.
- **In VS Code:** press **Cmd+Shift+P**, type `Reload Window` and press **Enter**.

Type `/mcp`: both servers must show as connected.

### 8 to 10

Do steps [8](#8-first-check-read-only), [9](#9-test-the-approval-window-and-click-cancel) and [10](#10-the-daily-api-limit) as on Windows. On a Mac the approval window is a standard macOS dialog with **Cancel** and **Approve**.

---

## Removing access

1. Delete the login folder: `%USERPROFILE%\.config\google-ads-mcp\` on Windows, `~/.config/google-ads-mcp/` on a Mac.
2. Remove the servers: `claude mcp remove google-ads -s user` and `claude mcp remove google-ads-builder -s user`.
3. Revoke the sign-in at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

---

## For the admin (once per company)

1. **Google Cloud project.** In [console.cloud.google.com](https://console.cloud.google.com), pick or create a project, ideally in your company's Google Workspace. Open **APIs & Services → Library → Google Ads API → Enable**. On the **Google Ads API** page, apply for API access: Google grants it to the Cloud project. See [access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).
2. **Sign-in screen.** In **Google Auth Platform → Audience**, click **Make internal** if you can, so only your company's accounts can sign in. Otherwise add each person under **Test users** (their sign-in then expires every 7 days) or publish the app.
3. **OAuth client.** In **Google Auth Platform → Clients → Create client**, choose **Desktop app** (not "Web application"), click **Create** and **Download JSON** at once. Don't delete this client later: that breaks everyone's sign-in. Share the JSON file privately.
4. **builder.toml.** Copy [`config/builder.toml.example`](../config/builder.toml.example) and fill it in: the ad account, its currency, a daily budget cap, the allowed apps and your website `domains`. Keep the folders as `~/...` paths so the same file works on Windows and Mac, and leave `codex_path` out so each computer uses its own default. To list the accounts and currencies your login can reach (read-only): `uv run --no-project --with google-ads python scripts/check_access.py`. Share the file privately.
5. **Access.** Give each person's Google account access to the Google Ads account. **Standard** access is enough; it can't manage users or billing.
