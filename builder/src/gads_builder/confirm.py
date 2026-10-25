"""The builder's own approval window, shown before every real change: a native Yes/No dialog on macOS
(osascript) and a small WinForms window on Windows (Windows PowerShell).

It does not depend on Claude Code's permission mode. Anything but a clear click on Approve is a No:
Cancel, closing the window, Enter or Esc, the timeout, a missing osascript or PowerShell, any error, or
any other system (such as Linux), where no window can open."""

from __future__ import annotations

import base64
import json
import ntpath
import os
import subprocess
import sys

from . import rules

OSASCRIPT = "/usr/bin/osascript"
MAX_LINES = 45  # a longer list is refused, never cut
MAX_LINE = 120
TOO_MANY_LINES = "too many items to show in one approval window; split the plan"
UNSUPPORTED = ("The builder's approval window opens only on macOS and Windows, so no change can be approved on "
               "this computer. Nothing was changed.")
_run = subprocess.run  # tests replace this


def _on_macos() -> bool:
    return sys.platform == "darwin"


def _on_windows() -> bool:
    return sys.platform == "win32"


def window_supported() -> bool:
    """Whether this computer can show the approval window at all (macOS or Windows)."""
    return _on_macos() or _on_windows()


class TailLine(str):
    """A line whose end matters most, such as a file path. If it is too long, the text after its
    label (`head`) is shortened from the LEFT, so the file name stays visible."""

    head: str

    def __new__(cls, head: str, tail: str):
        line = super().__new__(cls, head + tail)
        line.head = head
        return line


def quoted(text) -> str:
    """Free text in double quotes, with any quote or backslash inside escaped, so the person sees
    exactly where it starts and ends."""
    return json.dumps(str(text), ensure_ascii=False)


def quoted_tail(head: str, text) -> TailLine:
    """head + the quoted text, as a line that keeps its end when it is too long."""
    q = quoted(text)
    return TailLine(head + q[0], q[1:])


def _flat(ch: str) -> str:
    """A space for anything that could fake a line or hide: control, bidi, format (except ZWNJ and
    ZWJ, which Persian text and emoji need) and blank-looking letters such as the Hangul filler."""
    if rules.has_unsafe_characters(ch) or rules.has_invisible_characters(ch):
        return " "
    return ch


def _flat_line(text) -> str:
    """Line breaks, control, bidi, other invisible format characters and blank-looking letters become
    spaces (ZWNJ and ZWJ stay), runs of whitespace become one space, and the line has at most MAX_LINE
    characters."""
    line = " ".join("".join(map(_flat, str(text))).split())
    if len(line) <= MAX_LINE:
        return line
    head = getattr(text, "head", None)
    if isinstance(text, TailLine) and line.startswith(head) and len(head) < MAX_LINE // 2:
        return head + "…" + line[len(line) - (MAX_LINE - len(head) - 1):]
    return line[: MAX_LINE - 1] + "…"


def shown_lines(lines: list[str]) -> list[str]:
    """Exactly the lines the window shows. Refuses a list that would not fit, rather than cut it."""
    if len(lines) > MAX_LINES:
        raise ValueError(TOO_MANY_LINES)
    return [_flat_line(line) for line in lines]


def dialog_text(lines: list[str]) -> str:
    return "\n".join(shown_lines(lines))


def osascript_command(title: str, text: str, timeout_s: int) -> list[str]:
    """The title and text travel as arguments, never inside the AppleScript source."""
    script = (
        'display dialog (item 2 of argv) with title (item 1 of argv) buttons {"Cancel", "Approve"} '
        'default button "Cancel" cancel button "Cancel" with icon caution '
        f"giving up after {int(timeout_s)}"
    )
    return [OSASCRIPT, "-e", "on run argv", "-e", script, "-e", "end run", "--", title, text]


def approved(stdout: str) -> bool:
    """osascript prints the dialog's result as 'button returned:Approve, gave up:false'."""
    fields = {}
    for part in (stdout or "").strip().split(", "):
        key, sep, value = part.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields.get("button returned") == "Approve" and fields.get("gave up") == "false"


# ------------------------------------------------------------------------------------------- Windows
# The title and the text never go into the script or onto the command line. They travel in these
# environment variables as base64 of their UTF-8 bytes (only A-Z, a-z, 0-9, +, / and =), so no quote,
# $, backtick, line break or Arabic or Persian letter in a plan can change what PowerShell runs, and no
# file is left behind for anything else to change between writing and reading.
ENV_TITLE = "GADS_APPROVAL_TITLE_B64"
ENV_TEXT = "GADS_APPROVAL_TEXT_B64"
ENV_SECONDS = "GADS_APPROVAL_SECONDS"
APPROVED = "APPROVED"
CREATE_NO_WINDOW = 0x08000000  # no console window next to the approval window

# One fixed line of PowerShell: no double quotes and no backslashes, so the command line needs no escaping.
# Only a mouse click on Approve approves: Approve has no DialogResult and no tab stop, and its MouseClick
# event (which a key press never raises) is the only thing that sets OK. Cancel is the AcceptButton, the
# CancelButton and the focused control, so Enter and Esc cancel; closing the window or the timer cancels.
# The script prints APPROVED only after that click, and nothing else.
POWERSHELL_SCRIPT = "; ".join([
    "$ErrorActionPreference = 'Stop'",
    "Add-Type -AssemblyName System.Windows.Forms",
    "Add-Type -AssemblyName System.Drawing",
    "$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false, $true",
    f"$title = $utf8.GetString([Convert]::FromBase64String($env:{ENV_TITLE}))",
    f"$text = $utf8.GetString([Convert]::FromBase64String($env:{ENV_TEXT}))",
    f"$seconds = [int]::Parse($env:{ENV_SECONDS}, [Globalization.CultureInfo]::InvariantCulture)",
    "[Windows.Forms.Application]::EnableVisualStyles()",
    "$form = New-Object Windows.Forms.Form",
    "$form.Text = $title",
    "$form.TopMost = $true",
    "$form.StartPosition = 'CenterScreen'",
    "$form.MinimizeBox = $false",
    "$form.MaximizeBox = $false",
    "$form.Font = New-Object Drawing.Font -ArgumentList 'Segoe UI', 10",
    "$form.ClientSize = New-Object Drawing.Size -ArgumentList 860, 620",
    "$box = New-Object Windows.Forms.TextBox",
    "$box.Multiline = $true",
    "$box.ReadOnly = $true",
    "$box.WordWrap = $false",
    "$box.ScrollBars = 'Both'",
    "$box.Dock = 'Fill'",
    "$box.Lines = $text.Split([char]10)",
    "$buttons = New-Object Windows.Forms.FlowLayoutPanel",
    "$buttons.Dock = 'Bottom'",
    "$buttons.FlowDirection = 'RightToLeft'",
    "$buttons.AutoSize = $true",
    "$buttons.Padding = New-Object Windows.Forms.Padding -ArgumentList 8",
    "$cancel = New-Object Windows.Forms.Button",
    "$cancel.Text = 'Cancel'",
    "$cancel.AutoSize = $true",
    "$cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel",
    "$approve = New-Object Windows.Forms.Button",
    "$approve.Text = 'Approve'",
    "$approve.AutoSize = $true",
    "$approve.TabStop = $false",
    "$approve.DialogResult = [Windows.Forms.DialogResult]::None",
    "$approve.Add_MouseClick({ if ($_.Button -eq [Windows.Forms.MouseButtons]::Left) {"
    " $form.Tag = 'approved'; $form.DialogResult = [Windows.Forms.DialogResult]::OK } })",
    "$buttons.Controls.Add($cancel)",
    "$buttons.Controls.Add($approve)",
    "$form.Controls.Add($box)",
    "$form.Controls.Add($buttons)",
    "$form.AcceptButton = $cancel",
    "$form.CancelButton = $cancel",
    "$form.ActiveControl = $cancel",
    "$timer = New-Object Windows.Forms.Timer",
    "$timer.Interval = $seconds * 1000",
    "$timer.Add_Tick({ $timer.Stop(); $form.Tag = $null; $form.DialogResult = [Windows.Forms.DialogResult]::Cancel })",
    "$form.Add_Shown({ $form.Activate(); [void]$cancel.Focus() })",
    "$timer.Start()",
    "$result = $form.ShowDialog()",
    "$timer.Stop()",
    "if ($result -eq [Windows.Forms.DialogResult]::OK -and $form.Tag -eq 'approved') { [Console]::Out.Write('APPROVED') }",
])


def powershell_path() -> str | None:
    """Windows PowerShell (in every Windows 10 and 11) by its absolute path under %SystemRoot%, never
    looked up on PATH. None if SystemRoot is not an absolute path with a drive letter."""
    root = os.environ.get("SystemRoot", "")
    drive, _ = ntpath.splitdrive(root)
    if len(drive) != 2 or drive[1] != ":" or not ntpath.isabs(root):
        return None
    return ntpath.join(ntpath.normpath(root), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def powershell_command(powershell: str) -> list[str]:
    """Always the same command: the title and text are not in it (see powershell_env)."""
    return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-Command", POWERSHELL_SCRIPT]


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def powershell_env(title: str, text: str, timeout_s: int) -> dict[str, str]:
    """The inherited environment plus the window's title, text and timeout."""
    env = dict(os.environ)
    env.update({ENV_TITLE: _b64(title), ENV_TEXT: _b64(text), ENV_SECONDS: str(int(timeout_s))})
    return env


def windows_approved(returncode: int, stdout: str) -> bool:
    """Only exit code 0 with exactly APPROVED printed."""
    return returncode == 0 and isinstance(stdout, str) and stdout.strip() == APPROVED


def _ask_windows(title: str, text: str, timeout_s: int) -> bool:
    powershell = powershell_path()
    if powershell is None:
        return False
    proc = _run(
        powershell_command(powershell), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout_s + 30, stdin=subprocess.DEVNULL, env=powershell_env(title, text, timeout_s),
        creationflags=CREATE_NO_WINDOW,
    )
    return windows_approved(proc.returncode, proc.stdout)


# -------------------------------------------------------------------------------------------- either


def ask_person(title: str, lines: list[str], timeout_s: int = 60) -> bool:
    """True only if the person clicked Approve before the timeout. On any system but macOS and Windows,
    always False (the server says so first: see UNSUPPORTED)."""
    try:
        if _on_windows():
            timeout_s = max(10, min(int(timeout_s), 3600))
            return _ask_windows(_flat_line(title), dialog_text(lines), timeout_s)
        if not _on_macos():
            return False
        timeout_s = max(10, min(int(timeout_s), 3600))
        proc = _run(
            osascript_command(_flat_line(title), dialog_text(lines), timeout_s),
            capture_output=True, text=True, timeout=timeout_s + 30, stdin=subprocess.DEVNULL,
        )
        return proc.returncode == 0 and approved(proc.stdout)
    except Exception:
        return False
