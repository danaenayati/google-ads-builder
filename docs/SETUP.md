# Setup

About 40 minutes. Pick your computer: **[Windows](#windows)** or **[Mac](#mac)**.

**What you need:** a Google account that can open your Google Ads account in the Google Ads website (**Standard** access is enough). Everything else you make yourself in the steps below: your own Google sign-in file (step 3) and your own settings file, `builder.toml` (step 5). **Nobody needs to send you a file**, and no developer token is needed.

In a team that already shares one setup? See **[Sharing one setup with a team](#sharing-one-setup-with-a-team-optional)** at the end: you can use the team's sign-in file instead of making your own.

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

### 3. Make your own Google sign-in file (once, about 10 minutes)

The builder signs in to Google Ads with a sign-in file (an "OAuth client") from **your own** Google Cloud project. It is free.

1. Open [console.cloud.google.com](https://console.cloud.google.com) and sign in with **the Google account that has Google Ads access**. Accept the terms if Google asks.
2. **Make a project:** click the project picker at the top → **New project**. Name it, for example, `Google Ads builder`, and click **Create**. Make sure it is the selected project at the top.
3. **Turn on the Google Ads API:** open **APIs & Services → Library**, search for **Google Ads API**, open it and click **Enable**. On the same page, apply for API access if Google offers it: Google grants access to the Cloud project (see [access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels)).
4. **Sign-in screen:** open **Google Auth Platform** (search for it at the top) and click **Get started**. Give the app a name (such as `Google Ads builder`) and your email address, then:
   - If your Google account belongs to a company Google Workspace, choose **Internal**.
   - Otherwise choose **External**. Then open **Audience → Test users → Add users** and add **your own** email address. (While the app is in "Testing", Google asks you to sign in again every 7 days: just run step 4 again.)
5. **The sign-in file:** open **Google Auth Platform → Clients → Create client**. Choose **Desktop app** (not "Web application"), click **Create**, then **Download JSON**. Save the file in your **Downloads** folder. Its name looks like `client_secret_XXXX.json`.

Keep this file private: never put it on GitHub or in a shared chat. Don't delete the client later: that signs you out.

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

### 5. Make your settings (builder.toml)

This reads your Google Ads account (it changes nothing) and writes `builder.toml`, the builder's settings, next to your login:

```powershell
cd $HOME\google-ads-builder
uv run --no-project --with google-ads python scripts/make_settings.py
```

1. If your login can open several ad accounts, it lists them: type the number of the one to use. Accounts you reach through a manager account are listed too, and the script sets that up for you.
2. It suggests the rest from the account: the daily budget cap, the country, the websites your ads link to and the apps your App campaigns promote. Press **Enter** to accept a suggestion, or type your own.
3. It prints `Saved C:\Users\YOU\.config\google-ads-mcp\builder.toml`.

You can open `builder.toml` in Notepad later to change anything, such as `blocked_words` (competitor names that must never appear in your ads). Run the script again any time to start over; it keeps the old file as a backup.

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
4. A window titled **Google Ads: approve this change?** opens on top of your other windows. It lists the account, the campaign and the budget. On Windows, **Approve** stays greyed out for its first 1.5 seconds, and counts only a real click with your mouse or touchpad.
5. Click **Cancel**.
6. Claude answers `Not approved in the approval window (Cancel, closed or timed out). Nothing was changed.`

If no window appears, see [Troubleshooting](TROUBLESHOOTING.md).

**Try the window on its own (optional).** This opens the same window without Claude and without Google, so nothing can be sent. It prints `True` only when you click **Approve** with the mouse; Cancel, Enter, Esc, closing it or waiting 60 seconds print `False`. If the window can't open, it shows an error that says why:

```powershell
cd $HOME\google-ads-builder\builder
uv run --frozen python -c "from gads_builder import confirm; print(confirm.ask_person('Test window', ['Nothing is sent. Try Enter, Esc, Cancel and Approve.', 'Arabic: مرحبا  Persian: می‌خواهم']))"
```

On a Mac, run `cd ~/google-ads-builder/builder` first, then the same `uv run` line.

### 10. The daily API limit

Google gives your Cloud project a daily limit of API requests (everyone who uses the same sign-in file shares it). Big reports and checks use more of it.

- If it runs out, Claude shows: `the account's daily Google Ads API quota is used up (RESOURCE_EXHAUSTED)`. Nothing is broken.
- The limit resets at **midnight Pacific time** (Google's time zone for the quota).
- If you run out often, apply for a higher access level for your Cloud project, such as **Basic**. See Google's [access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).

### Optional: images with Codex

`generate_image` makes ad images with Codex and your ChatGPT Plus allowance. Without Codex, Claude asks you for image files instead.

1. Install [Node.js](https://nodejs.org) (the LTS version). Close PowerShell and open it again.
2. Install Codex and sign in to ChatGPT once:

   ```powershell
   npm install -g @openai/codex
   codex login
   ```

   If PowerShell says `running scripts is disabled on this system`, type `npm.cmd` and `codex.cmd` instead of `npm` and `codex`.

3. Restart Claude Code. The builder finds the real program, `codex.exe`, where npm puts it:

   `C:\Users\YOU\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`

   On an ARM PC the folders are `codex-win32-arm64` and `aarch64-pc-windows-msvc`. This shows the file if it is there:

   ```powershell
   Get-ChildItem "$env:APPDATA\npm\node_modules\@openai\codex\node_modules\@openai\*\vendor\*\bin\codex.exe"
   ```

Is your `codex.exe` somewhere else? Put its full path in `builder.toml`, with forward slashes, such as `codex_path = "C:/Tools/codex/codex.exe"`.

On Windows the builder runs only `codex.exe`, by its full path. It never runs `codex.cmd` or `codex.bat` (what npm puts on `PATH`), and it never takes a `codex.exe` from the folder Claude Code was started in, where anyone could have left one.

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

### 3. Make your own Google sign-in file

Do [step 3 of the Windows guide](#3-make-your-own-google-sign-in-file-once-about-10-minutes): it happens in the browser and is the same on a Mac. Save the downloaded `client_secret_XXXX.json` in your **Downloads** folder.

### 4. Sign in to Google once

```bash
cd ~/google-ads-builder
uv run --no-project --with google-auth-oauthlib python scripts/signin.py ~/Downloads/client_secret_XXXX.json
```

Sign in with the Google account that has Google Ads access, and click **Allow**. The script saves your login in `~/.config/google-ads-mcp/` (a folder only your Mac user can open). Signed in to several Google accounts? Put `GADS_NO_BROWSER=1 ` in front of the command and open the printed link in a private window.

### 5. Make your settings (builder.toml)

```bash
cd ~/google-ads-builder
uv run --no-project --with google-ads python scripts/make_settings.py
```

Pick your ad account by its number and press **Enter** to accept each suggestion, as described for [Windows](#5-make-your-settings-buildertoml). Then check that the builder starts with it:

```bash
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

Do steps [8](#8-first-check-read-only), [9](#9-test-the-approval-window-and-click-cancel) and [10](#10-the-daily-api-limit) as on Windows. On a Mac the approval window is a standard macOS dialog with **Cancel** and **Approve**. Never give Terminal, iTerm or VS Code **Accessibility** permission (System Settings → Privacy & Security → Accessibility): with it, a script Claude runs could click **Approve** for you.

For `generate_image` on a Mac, install the ChatGPT app and sign in: the builder uses the Codex inside it.

---

## Removing access

1. Delete the login folder: `%USERPROFILE%\.config\google-ads-mcp\` on Windows, `~/.config/google-ads-mcp/` on a Mac.
2. Remove the servers: `claude mcp remove google-ads -s user` and `claude mcp remove google-ads-builder -s user`.
3. Revoke the sign-in at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
4. If you made your own Cloud project only for this, you can delete it in [console.cloud.google.com](https://console.cloud.google.com) (**IAM & Admin → Settings → Shut down**).

---

## Sharing one setup with a team (optional)

A team can share one Cloud project instead of each person making their own. One person (the admin) does this once:

1. Make the Cloud project, turn on the Google Ads API and make the sign-in screen as in [step 3](#3-make-your-own-google-sign-in-file-once-about-10-minutes). Choose **Internal** if everyone is in the company's Google Workspace; otherwise add each person under **Test users**, or publish the app.
2. Make one **Desktop app** client and download its JSON. Share it **privately** (in person, in a password manager or a private chat; never on GitHub or in a shared channel). Don't delete this client later: that signs everyone out.
3. Give each person's Google account access to the Google Ads account. **Standard** access is enough; it can't manage users or billing.

Each person then follows this guide, using the shared file in step 4 instead of making one in step 3, and runs `make_settings.py` in step 5 as usual. Everyone who uses the same file shares one daily API limit.
