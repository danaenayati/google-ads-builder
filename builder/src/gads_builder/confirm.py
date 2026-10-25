"""The builder's own approval window, shown before every real change: a native Yes/No dialog on macOS
(osascript) and a small WinForms window on Windows (Windows PowerShell).

It does not depend on Claude Code's permission mode. Anything but a clear click on Approve is a No:
Cancel, closing the window, Enter or Esc, the timeout, or any error. A window that could not open at all
(a missing or locked-down osascript or PowerShell, another approval window already open, or any other
system, such as Linux) is not a person's No: ask_person raises WindowUnavailable instead, with a message
that says why."""

from __future__ import annotations

import base64
import contextlib
import json
import ntpath
import os
import string
import subprocess
import sys
from pathlib import Path

from . import rules

OSASCRIPT = "/usr/bin/osascript"
MAX_LINES = 45  # a longer list is refused, never cut
MAX_LINE = 120
TOO_MANY_LINES = "too many items to show in one approval window; split the plan"
UNSUPPORTED = ("The builder's approval window opens only on macOS and Windows, so no change can be approved on "
               "this computer. Nothing was changed.")
ANOTHER_WINDOW = ("Another approval window is already open on this computer (maybe from another Claude Code "
                  "session); answer it first. Nothing was changed.")
_run = subprocess.run  # tests replace this


class WindowUnavailable(Exception):
    """No approval window could be shown, so nobody answered: never approved, and not a person's No."""


def couldnt_start(reason: str, where: str) -> str:
    return f"The approval window couldn't start on this {where}: {reason}. Nothing was changed."


def _why(error: BaseException) -> str:
    return _flat_line(f"{type(error).__name__}: {error}").rstrip(". ")


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


# --------------------------------------------------------------------------------------------- macOS


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


# One approval window at a time for this user, whichever Claude Code session (server process) asks: an
# exclusive lock on this file, taken without waiting. Windows uses a named mutex in its script instead.
DEFAULT_LOCK_PATH = Path.home() / ".config" / "google-ads-mcp" / "approval-window.lock"
LOCK_PATH = DEFAULT_LOCK_PATH  # tests point this at a file of their own


def _try_lock(fd: int) -> bool:
    """An exclusive lock on the open file, or False at once if another open file holds it."""
    try:
        import fcntl
    except ImportError:  # Windows, where only the tests take this lock
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


@contextlib.contextmanager
def _one_window_at_a_time():
    """Holds the approval-window lock, or raises WindowUnavailable(ANOTHER_WINDOW) at once if another
    process (or another open of the file) holds it. The lock ends with the file, so a crashed server never
    leaves it behind. The file is never followed through a symlink."""
    path = Path(LOCK_PATH)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o600)
    except OSError as e:
        raise WindowUnavailable(couldnt_start(f"its lock file could not be opened ({_why(e)})", "Mac")) from None
    try:
        if not _try_lock(fd):
            raise WindowUnavailable(ANOTHER_WINDOW)
        yield
    finally:
        os.close(fd)


def _ask_macos(title: str, text: str, timeout_s: int) -> bool:
    with _one_window_at_a_time():
        try:
            proc = _run(osascript_command(title, text, timeout_s), capture_output=True, text=True,
                        timeout=timeout_s + 30, stdin=subprocess.DEVNULL)
        except OSError as e:
            raise WindowUnavailable(couldnt_start(f"osascript could not run ({_why(e)})", "Mac")) from None
    if proc.returncode == 0:
        return approved(proc.stdout)  # Approve, or Cancel or the timeout ("gave up:true")
    if "(-128)" in (proc.stderr or ""):
        return False  # Cancel or Esc: "User canceled. (-128)"
    error = _flat_line(proc.stderr or "").rstrip(". ") or f"osascript ended with exit code {proc.returncode}"
    raise WindowUnavailable(couldnt_start(error, "Mac"))


# ------------------------------------------------------------------------------------------- Windows
# The title and the text never go into the script or onto the command line. They travel in these
# environment variables as base64 of their UTF-8 bytes (only A-Z, a-z, 0-9, +, / and =), so no quote,
# $, backtick, line break or Arabic or Persian letter in a plan can change what PowerShell runs, and no
# file is left behind for anything else to change between writing and reading.
ENV_TITLE = "GADS_APPROVAL_TITLE_B64"
ENV_TEXT = "GADS_APPROVAL_TEXT_B64"
ENV_SECONDS = "GADS_APPROVAL_SECONDS"
APPROVED = "APPROVED"
DECLINED = "DECLINED"
BUSY = "BUSY"
UNAVAILABLE = "UNAVAILABLE"
EXIT_UNAVAILABLE = 3  # the script couldn't set up the window (Add-Type refused, no hook, ...)
EXIT_BUSY = 4  # another approval window holds the mutex
ARM_MS = 1500  # Approve stays disabled this long after the window shows (and after any move)
CREATE_NO_WINDOW = 0x08000000  # no console window next to the approval window

# The guard against fake clicks, compiled by Add-Type inside the window's PowerShell. Fixed, reviewed C#
# (C# 5, which Windows PowerShell 5.1 compiles): it never holds plan text, reads nothing from outside, and
# approves nothing itself. It needs quotes and a backslash, so the script carries it as base64 (GUARD_B64).
GUARD_SOURCE = r"""// Windows PowerShell's Add-Type treats compiler warnings as errors; the structs below are filled by
// Windows, never by this code, which is what warnings 649 and 169 are about.
#pragma warning disable 649, 169
using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

// The approval window's guard against fake clicks (google-ads-builder, confirm.py). The window's script
// asks Check() on every click of Approve, and approves only on its empty answer. A click counts only if
// this low-level mouse hook saw a physical left button down and then up (without the LLMHF_INJECTED or
// LLMHF_LOWER_IL_INJECTED flag that synthesized input carries), both on Approve, where Approve has stood
// still since it was armed, with the up at most ClickWindowMs before Check(). Mouse messages posted to the
// window (WM_LBUTTONDOWN/UP, BM_CLICK) never pass the hook, so they never count. Approve is disabled until
// its place on screen has stayed the same for the arming delay, and again after any move or resize.
public static class GadsApprovalGuard
{
    public const int ClickWindowMs = 300;
    const int WH_MOUSE_LL = 14;
    const int WM_LBUTTONDOWN = 0x0201;
    const int WM_LBUTTONUP = 0x0202;
    const uint LLMHF_INJECTED = 0x00000001;
    const uint LLMHF_LOWER_IL_INJECTED = 0x00000002;

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int X; public int Y; }

    [StructLayout(LayoutKind.Sequential)]
    struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    struct MSLLHOOKSTRUCT { public POINT Pt; public uint MouseData; public uint Flags; public uint Time; public IntPtr ExtraInfo; }

    delegate IntPtr LowLevelMouseProc(int nCode, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    static extern IntPtr SetWindowsHookExW(int idHook, LowLevelMouseProc lpfn, IntPtr hMod, uint dwThreadId);

    [DllImport("user32.dll")]
    static extern bool UnhookWindowsHookEx(IntPtr hhk);

    [DllImport("user32.dll")]
    static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr GetModuleHandleW(string lpModuleName);

    [DllImport("user32.dll")]
    static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    static extern IntPtr WindowFromPoint(POINT pt);

    [DllImport("user32.dll")]
    static extern IntPtr WindowFromPhysicalPoint(POINT pt);

    [DllImport("user32.dll")]
    static extern IntPtr SetThreadDpiAwarenessContext(IntPtr dpiContext);

    static readonly LowLevelMouseProc HookProc = OnMouse;  // a field, so the delegate lives as long as the hook
    static Mutex windowLock;
    static bool ownsWindowLock;
    static IntPtr hook = IntPtr.Zero;
    static Control approve;
    static System.Windows.Forms.Timer armTimer;
    static int armDelayMs;
    static bool placed;
    static RECT place;
    static int placedAt;
    static bool armed;
    static bool downOnApprove;
    static bool upOnApprove;
    static int upAt;
    static bool lastInjected;

    // One approval window at a time in this Windows session, whichever Claude Code session asks. Never waits.
    public static bool TakeWindowLock()
    {
        windowLock = new Mutex(false, @"Local\GadsBuilderApproval");
        try
        {
            ownsWindowLock = windowLock.WaitOne(0, false);
        }
        catch (AbandonedMutexException)
        {
            ownsWindowLock = true;  // the window that held it ended without letting go: it is ours now
        }
        return ownsWindowLock;
    }

    // Approve starts disabled, and the hook watches the left button from now until Stop().
    public static void Start(Control button, int delayMs)
    {
        approve = button;
        armDelayMs = delayMs;
        approve.Enabled = false;
        hook = SetWindowsHookExW(WH_MOUSE_LL, HookProc, GetModuleHandleW(null), 0);
        if (hook == IntPtr.Zero)
        {
            throw new InvalidOperationException("the mouse check could not start (error " + Marshal.GetLastWin32Error() + ")");
        }
        armTimer = new System.Windows.Forms.Timer();
        armTimer.Interval = 50;
        armTimer.Tick += OnTick;
        armTimer.Start();
    }

    public static void Stop()
    {
        try
        {
            if (armTimer != null) armTimer.Stop();
            if (hook != IntPtr.Zero) UnhookWindowsHookEx(hook);
            hook = IntPtr.Zero;
            if (ownsWindowLock) windowLock.ReleaseMutex();
            ownsWindowLock = false;
        }
        catch (Exception)
        {
            // the window is closed and its answer decided; the process ends next, which frees both anyway
        }
    }

    // "" if Approve's click now is a physical one; otherwise why not.
    public static string Check()
    {
        if (!armed) return "Approve was not armed yet";
        if (lastInjected) return "injected input";
        bool clicked = upOnApprove;
        upOnApprove = false;  // one physical click approves once at most
        if (!clicked) return "no physical click on Approve";
        int age = unchecked(Environment.TickCount - upAt);
        if (age < 0 || age > ClickWindowMs) return "the physical click was too long ago";
        RECT now;
        if (!ApproveRect(out now) || !Same(now, place)) return "Approve moved";
        return "";
    }

    static void OnTick(object sender, EventArgs e)
    {
        RECT now;
        if (!ApproveRect(out now))
        {
            placed = false;
            Disarm();
            return;
        }
        if (!placed || !Same(now, place))
        {
            place = now;
            placed = true;
            placedAt = Environment.TickCount;
            Disarm();
            return;
        }
        if (!armed && unchecked(Environment.TickCount - placedAt) >= armDelayMs)
        {
            armed = true;
            approve.Enabled = true;
        }
    }

    static void Disarm()
    {
        armed = false;
        downOnApprove = false;
        upOnApprove = false;
        if (approve.Enabled) approve.Enabled = false;
    }

    static IntPtr OnMouse(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode >= 0)
        {
            try
            {
                long message = wParam.ToInt64();
                if (message == WM_LBUTTONDOWN || message == WM_LBUTTONUP)
                {
                    MSLLHOOKSTRUCT info = (MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(MSLLHOOKSTRUCT));
                    bool injected = (info.Flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED)) != 0;
                    bool onApprove = !injected && armed && IsOnApprove(info.Pt);
                    lastInjected = injected;
                    if (message == WM_LBUTTONDOWN)
                    {
                        downOnApprove = onApprove;
                        upOnApprove = false;
                    }
                    else
                    {
                        upOnApprove = onApprove && downOnApprove;
                        downOnApprove = false;
                        upAt = Environment.TickCount;
                    }
                }
            }
            catch (Exception)
            {
                downOnApprove = false;
                upOnApprove = false;
            }
        }
        return CallNextHookEx(hook, nCode, wParam, lParam);
    }

    static bool ApproveRect(out RECT rect)
    {
        rect = new RECT();
        return approve != null && approve.IsHandleCreated && approve.Visible && GetWindowRect(approve.Handle, out rect);
    }

    static bool Same(RECT a, RECT b)
    {
        return a.Left == b.Left && a.Top == b.Top && a.Right == b.Right && a.Bottom == b.Bottom;
    }

    // The point is on Approve, nothing covers Approve there, and Approve has not moved since it was armed.
    static bool IsOnApprove(POINT pt)
    {
        RECT now;
        return ApproveRect(out now) && Same(now, place) && WindowAtPhysicalPoint(pt) == approve.Handle;
    }

    // The hook reports physical screen pixels. Ask which window is there in the same terms, whatever the
    // display scaling and this program's DPI awareness: per-monitor aware for this one call.
    static IntPtr WindowAtPhysicalPoint(POINT pt)
    {
        IntPtr previous = IntPtr.Zero;
        try
        {
            previous = SetThreadDpiAwarenessContext(new IntPtr(-4));  // per-monitor aware, version 2
            if (previous == IntPtr.Zero) previous = SetThreadDpiAwarenessContext(new IntPtr(-3));  // per-monitor aware
        }
        catch (EntryPointNotFoundException)
        {
            previous = IntPtr.Zero;  // before Windows 10 version 1607
        }
        if (previous == IntPtr.Zero) return WindowFromPhysicalPoint(pt);
        try
        {
            return WindowFromPoint(pt);
        }
        finally
        {
            SetThreadDpiAwarenessContext(previous);
        }
    }
}
"""
GUARD_B64 = base64.b64encode(GUARD_SOURCE.encode("ascii")).decode("ascii")

# Fixed PowerShell: no double quotes and no backslashes, so the command line needs no escaping.
# Only a physical mouse click on Approve approves: Approve has no DialogResult and no tab stop, and its
# MouseClick event (which a key press never raises) sets OK only when the guard's Check() accepts the click.
# Cancel is the AcceptButton, the CancelButton and the focused control, so Enter and Esc cancel; closing
# the window or the timer cancels. Everything that can fail before the window shows is inside try, and a
# failure there prints UNAVAILABLE (exit 3): it is not a No. Another open approval window prints BUSY
# (exit 4). After the window closes, the script prints APPROVED or DECLINED, and nothing else.
_SETUP = [
    "Add-Type -AssemblyName System.Windows.Forms",
    "Add-Type -AssemblyName System.Drawing",
    f"$guardSource = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{GUARD_B64}'))",
    "Add-Type -TypeDefinition $guardSource -ReferencedAssemblies System.Windows.Forms, System.Drawing",
    "$owner = [GadsApprovalGuard]::TakeWindowLock()",
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
    " $why = [GadsApprovalGuard]::Check(); if ($why -eq '') { $form.Tag = 'approved';"
    " $form.DialogResult = [Windows.Forms.DialogResult]::OK } else {"
    " [Console]::Error.WriteLine('refused click: ' + $why) } } })",
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
    f"[GadsApprovalGuard]::Start($approve, {ARM_MS})",
]
_SHOW = [
    f"if (-not $owner) {{ [Console]::Out.Write('{BUSY}'); exit {EXIT_BUSY} }}",
    "$timer.Start()",
    "$result = $form.ShowDialog()",
    "$timer.Stop()",
    "[GadsApprovalGuard]::Stop()",
    f"if ($result -eq [Windows.Forms.DialogResult]::OK -and $form.Tag -eq 'approved') "
    f"{{ [Console]::Out.Write('{APPROVED}') }} else {{ [Console]::Out.Write('{DECLINED}') }}",
]
POWERSHELL_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; try { " + "; ".join(_SETUP)
    + f" }} catch {{ '{UNAVAILABLE} ' + $_.Exception.Message; exit {EXIT_UNAVAILABLE} }}; " + "; ".join(_SHOW)
)

# What powershell.exe gets from the server's environment: only what Windows and PowerShell need to start
# and to compile the guard (TEMP). Never PATH, PSModulePath, __PSLockdownPolicy or the COR_* profiler
# variables, which could load other code into the window's process.
_PASSED_ENV = ("TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PATHEXT")


def _windows_folders() -> tuple[str, str] | None:
    """(System32, the Windows folder) as Windows itself reports them (GetSystemDirectoryW and
    GetSystemWindowsDirectoryW), never from environment variables such as SystemRoot. None where there is
    no Windows API to ask."""
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (ImportError, AttributeError, OSError):
        return None
    folders = []
    for function in (kernel32.GetSystemDirectoryW, kernel32.GetSystemWindowsDirectoryW):
        function.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
        function.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(1024)
        length = function(buffer, len(buffer))
        if not 0 < length < len(buffer):
            return None
        folders.append(buffer.value)
    return folders[0], folders[1]


def _drive_path(path) -> bool:
    """An absolute path on a drive letter (C:\\...) with no ':' after the drive: never a relative, network
    or device path, or an NTFS stream."""
    if not isinstance(path, str):
        return False
    drive, rest = ntpath.splitdrive(path)
    return (len(drive) == 2 and drive[0] in string.ascii_letters and drive[1] == ":" and ":" not in rest
            and rest[:1] in ("\\", "/") and bool(rest.strip("\\/")))


def windows_folders() -> tuple[str, str] | None:
    """(System32, the Windows folder), normalised, if Windows reported usable ones; otherwise None."""
    folders = _windows_folders()
    if not isinstance(folders, tuple) or len(folders) != 2 or not all(map(_drive_path, folders)):
        return None
    return ntpath.normpath(folders[0]), ntpath.normpath(folders[1])


def powershell_path(system32: str) -> str:
    """Windows PowerShell (in every Windows 10 and 11) by its absolute path under System32, never looked up
    on PATH."""
    return ntpath.join(ntpath.normpath(system32), "WindowsPowerShell", "v1.0", "powershell.exe")


def powershell_command(powershell: str) -> list[str]:
    """Always the same command: the title and text are not in it (see powershell_env)."""
    return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-Command", POWERSHELL_SCRIPT]


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def powershell_env(title: str, text: str, timeout_s: int, system32: str, windows_dir: str,
                   base=None) -> dict[str, str]:
    """A minimal environment: the window's title, text and timeout, the real Windows folder as SystemRoot
    and windir, cmd.exe in System32 as COMSPEC, and _PASSED_ENV from `base` (the server's environment;
    names compared without regard to case, as Windows does; empty values left out). Nothing else."""
    source = os.environ if base is None else base
    upper: dict[str, str] = {}
    for name, value in source.items():
        upper.setdefault(name.upper(), value)
    env = {name: upper[name] for name in _PASSED_ENV if upper.get(name)}
    env.update({"SystemRoot": windows_dir, "windir": windows_dir, "COMSPEC": ntpath.join(system32, "cmd.exe"),
                ENV_TITLE: _b64(title), ENV_TEXT: _b64(text), ENV_SECONDS: str(int(timeout_s))})
    return env


def windows_launch(title: str, text: str, timeout_s: int) -> dict:
    """How the window's PowerShell is started: {"args", "env", "cwd"}. It runs in System32, so nothing is
    loaded from the folder Claude Code was started in."""
    folders = windows_folders()
    if folders is None:
        raise WindowUnavailable(couldnt_start("Windows did not report its System32 folder", "PC"))
    system32, windows_dir = folders
    return {"args": powershell_command(powershell_path(system32)),
            "env": powershell_env(title, text, timeout_s, system32, windows_dir), "cwd": system32}


def windows_approved(returncode: int, stdout: str) -> bool:
    """Only exit code 0 with exactly APPROVED printed."""
    return returncode == 0 and isinstance(stdout, str) and stdout.strip() == APPROVED


def windows_answer(returncode: int, stdout: str) -> bool:
    """True for Approve, False for the person's No (DECLINED: Cancel, Enter, Esc, closed or timed out).
    Anything else means the window couldn't be shown or failed: WindowUnavailable, saying why."""
    out = stdout.strip() if isinstance(stdout, str) else ""
    if windows_approved(returncode, stdout):
        return True
    if returncode == 0 and out == DECLINED:
        return False
    if returncode == EXIT_BUSY and out == BUSY:
        raise WindowUnavailable(ANOTHER_WINDOW)
    if returncode == EXIT_UNAVAILABLE and out.startswith(UNAVAILABLE):
        reason = _flat_line(out[len(UNAVAILABLE):]).rstrip(". ")
        raise WindowUnavailable(couldnt_start(reason or "PowerShell gave no reason", "PC"))
    raise WindowUnavailable(couldnt_start(f"PowerShell ended with exit code {returncode} and no answer", "PC"))


def _ask_windows(title: str, text: str, timeout_s: int) -> bool:
    launch = windows_launch(title, text, timeout_s)
    try:
        proc = _run(
            launch["args"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s + 30, stdin=subprocess.DEVNULL, env=launch["env"], cwd=launch["cwd"],
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as e:
        raise WindowUnavailable(couldnt_start(f"Windows PowerShell could not run ({_why(e)})", "PC")) from None
    return windows_answer(proc.returncode, proc.stdout)


# -------------------------------------------------------------------------------------------- either


def ask_person(title: str, lines: list[str], timeout_s: int = 60) -> bool:
    """True only if the person clicked Approve before the timeout; False if they said No (Cancel, Enter,
    Esc, closing the window, the timeout) or anything failed once the window was up. Raises
    WindowUnavailable when no window could be shown: on any system but macOS and Windows (UNSUPPORTED),
    with a missing or locked-down osascript or PowerShell, or while another approval window is open."""
    try:
        if not (_on_macos() or _on_windows()):
            raise WindowUnavailable(UNSUPPORTED)
        timeout_s = max(10, min(int(timeout_s), 3600))
        try:
            shown_title, text = _flat_line(title), dialog_text(lines)
        except ValueError as e:
            raise WindowUnavailable(f"{e}. Nothing was changed.") from None
        if _on_windows():
            return _ask_windows(shown_title, text, timeout_s)
        return _ask_macos(shown_title, text, timeout_s)
    except WindowUnavailable:
        raise
    except OSError as e:
        raise WindowUnavailable(couldnt_start(_why(e), "PC" if _on_windows() else "Mac")) from None
    except Exception:
        return False
