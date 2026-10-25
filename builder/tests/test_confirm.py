import base64
import ntpath
import os
import re
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from gads_builder import confirm
from gads_builder.confirm import WindowUnavailable
from gads_builder.confirm import ask_person as real_ask_person  # the autouse guard replaces confirm.ask_person
from gads_builder.confirm import window_supported as real_window_supported  # and window_supported

APPROVE = "button returned:Approve, gave up:false\n"
USER_CANCELED = "0:183: execution error: User canceled. (-128)\n"
WIN_APPROVE = "APPROVED\r\n"
SYSTEM32 = "C:\\Windows\\System32"
WINDIR = "C:\\Windows"
POWERSHELL = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
REALLY_WINDOWS = sys.platform == "win32"  # read now: the mac and windows fixtures change sys.platform


def fake_run(calls, *, returncode=0, stdout=APPROVE, stderr="", raises=None):
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return run


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(confirm.sys, "platform", "darwin")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(confirm.sys, "platform", "win32")
    monkeypatch.setattr(confirm, "_windows_folders", lambda: (SYSTEM32, WINDIR))  # what Windows itself reports
    monkeypatch.setenv("SystemRoot", "D:\\Planted")  # never trusted


def test_the_text_travels_as_arguments_not_as_script(mac, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls))
    sneaky = 'x" & (do shell script "touch /tmp/pwned") & "'
    assert real_ask_person("Approve?", ["Account: 1234567890", sneaky], timeout_s=60) is True
    [(args, kwargs)] = calls
    assert args[:7] == ["/usr/bin/osascript", "-e", "on run argv", "-e", args[4], "-e", "end run"]
    assert args[7:] == ["--", "Approve?", f"Account: 1234567890\n{sneaky}"]
    assert args[4] == ('display dialog (item 2 of argv) with title (item 1 of argv) buttons {"Cancel", "Approve"} '
                       'default button "Cancel" cancel button "Cancel" with icon caution giving up after 60')
    assert "touch" not in args[4] and "1234567890" not in args[4]
    assert kwargs["timeout"] == 90 and kwargs["capture_output"] is True and kwargs["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("returncode, stdout, stderr, expected", [
    (0, "button returned:Approve, gave up:false\n", "", True),
    (0, "button returned:, gave up:true\n", "", False),  # timed out
    (0, "button returned:Approve, gave up:true\n", "", False),
    (1, "", USER_CANCELED, False),  # Cancel or Esc
    (0, "button returned:Cancel, gave up:false\n", "", False),
    (0, "", "", False),
    (0, "button returned:Approved, gave up:false\n", "", False),
    (1, "button returned:Approve, gave up:false\n", USER_CANCELED, False),
])
def test_only_a_clear_approve_counts(mac, monkeypatch, returncode, stdout, stderr, expected):
    monkeypatch.setattr(confirm, "_run", fake_run([], returncode=returncode, stdout=stdout, stderr=stderr))
    assert real_ask_person("t", ["x"]) is expected


@pytest.mark.parametrize("returncode, stderr", [
    (1, "execution error: No user interaction allowed. (-1713)"),
    (1, ""),
    (2, "osascript: no such file"),
    (-9, ""),
])
def test_a_dialog_that_could_not_open_is_not_a_no(mac, monkeypatch, returncode, stderr):
    """Only Cancel (-128), the timeout or another answer is a person's No; any other error means no dialog."""
    monkeypatch.setattr(confirm, "_run", fake_run([], returncode=returncode, stdout="", stderr=stderr))
    with pytest.raises(WindowUnavailable, match=r"^The approval window couldn't start on this Mac: .*Nothing was changed\.$"):
        real_ask_person("t", ["x"])


@pytest.mark.parametrize("error, unavailable", [
    (FileNotFoundError("no osascript"), True), (PermissionError("denied"), True), (OSError("boom"), True),
    (subprocess.TimeoutExpired("osascript", 330), False), (RuntimeError("anything"), False),
])
def test_any_failure_is_a_no_or_unavailable(mac, monkeypatch, error, unavailable):
    monkeypatch.setattr(confirm, "_run", fake_run([], raises=error))
    if unavailable:
        with pytest.raises(WindowUnavailable, match="couldn't start on this Mac"):
            real_ask_person("t", ["x"])
    else:
        assert real_ask_person("t", ["x"]) is False


def test_a_missing_osascript_is_unavailable(mac, monkeypatch, tmp_path):
    monkeypatch.setattr(confirm, "OSASCRIPT", str(tmp_path / "no-such-osascript"))
    monkeypatch.setattr(confirm, "_run", subprocess.run)  # the real runner, put back on purpose: no osascript
    with pytest.raises(WindowUnavailable, match="couldn't start on this Mac"):
        real_ask_person("t", ["x"])  # subprocess.run raises FileNotFoundError


@pytest.mark.parametrize("platform", ["linux", "freebsd14", "cygwin", "emscripten"])
def test_any_other_system_is_unavailable_and_runs_nothing(monkeypatch, platform):
    calls = []
    monkeypatch.setattr(confirm.sys, "platform", platform)
    monkeypatch.setattr(confirm, "_windows_folders", lambda: (SYSTEM32, WINDIR))
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    with pytest.raises(WindowUnavailable) as raised:
        real_ask_person("t", ["x"])
    assert str(raised.value) == confirm.UNSUPPORTED and calls == []
    assert real_window_supported() is False
    assert "only on macOS and Windows" in confirm.UNSUPPORTED and "Nothing was changed" in confirm.UNSUPPORTED


@pytest.mark.parametrize("platform, program", [("darwin", "/usr/bin/osascript"), ("win32", POWERSHELL)])
def test_macos_and_windows_each_open_their_own_window(monkeypatch, platform, program):
    calls = []
    monkeypatch.setattr(confirm.sys, "platform", platform)
    monkeypatch.setattr(confirm, "_windows_folders", lambda: (SYSTEM32, WINDIR))
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=APPROVE if platform == "darwin" else WIN_APPROVE))
    assert real_window_supported() is True
    assert real_ask_person("t", ["x"]) is True
    [(args, _)] = calls
    assert args[0] == program


def test_the_timeout_is_clamped(mac, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls))
    real_ask_person("t", ["x"], timeout_s=1)
    real_ask_person("t", ["x"], timeout_s=99999)
    assert [c[0][4].rsplit(" ", 1)[1] for c in calls] == ["10", "3600"]


def test_a_list_too_long_for_the_window_is_refused_never_cut():
    assert confirm.MAX_LINES == 45
    fits = [f"line {i}" for i in range(45)]
    assert confirm.dialog_text(fits).split("\n") == fits
    with pytest.raises(ValueError, match="too many items to show in one approval window; split the plan"):
        confirm.dialog_text(fits + ["line 45"])
    with pytest.raises(ValueError, match="split the plan"):
        confirm.shown_lines(fits + ["line 45"])


def test_a_too_long_list_opens_nothing(mac, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls))
    with pytest.raises(WindowUnavailable, match="split the plan"):
        real_ask_person("t", ["x"] * 46)
    assert calls == []


def test_long_lines_are_cut_and_fake_lines_are_flattened():
    assert confirm.dialog_text(["x" * 200]) == "x" * 119 + "…"
    assert confirm.dialog_text(["Campaign: A\nDaily budget: 1.00", "B\u202eC"]) == "Campaign: A Daily budget: 1.00\nB C"


@pytest.mark.parametrize("text, shown", [
    ("a  \t b", "a b"),  # runs of whitespace become one space
    ("  padded  ", "padded"),
    ("a\u00a0\u2003\u3000b", "a b"),  # other spaces too
    ("Pul\u200bse", "Pul se"),  # zero-width space (Cf)
    ("Pul\u2060se\ufeff", "Pul se"),  # word joiner, BOM
    ("soft\u00adhyphen", "soft hyphen"),
    ("tag\U000e0041s", "tag s"),  # tag characters
    ("a\u202eb\u2066c", "a b c"),  # bidi controls
    ("Pulse\u3164\u3164\u115f\u1160\uffa0\u2800x", "Pulse x"),  # blank-looking letters
    ("می\u200cخواهم", "می\u200cخواهم"),  # ZWNJ stays: Persian needs it
    ("👩\u200d💻", "👩\u200d💻"),  # ZWJ stays
])
def test_invisible_and_format_characters_are_flattened(text, shown):
    assert confirm.dialog_text([text]) == shown


def test_quoted_free_text_shows_where_it_starts_and_ends():
    assert confirm.quoted("Springfield Gyms") == '"Springfield Gyms"'
    assert confirm.quoted('Pulse" (PAUSED) "x') == '"Pulse\\" (PAUSED) \\"x"'
    assert confirm.quoted("a\\b") == '"a\\\\b"'
    assert confirm.quoted("می\u200cخواهم") == '"می\u200cخواهم"'  # not escaped to \\u


def test_a_long_path_keeps_its_end():
    path = "/Users/example/" + "deep/" * 30 + "square-final.png"
    line = confirm.quoted_tail("- image: ", path)
    assert line == f'- image: "{path}"'  # the window gets the whole line
    [shown] = confirm.shown_lines([line])
    assert len(shown) == confirm.MAX_LINE
    assert shown.startswith('- image: "…') and shown.endswith('deep/square-final.png"')
    assert confirm.shown_lines([confirm.quoted_tail("- ", "/short/a.png")]) == ['- "/short/a.png"']
    assert confirm.shown_lines(["x" * 200]) == ["x" * 119 + "…"]  # other lines keep their start


def test_the_title_is_one_line(mac, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls))
    real_ask_person("Approve\nthis", ["x"])
    assert calls[0][0][8] == "Approve this"


def test_the_guard_stops_real_dialogs_in_tests():
    with pytest.raises(RuntimeError, match="approval window"):
        confirm.ask_person("t", ["x"])


def test_the_guard_stops_the_subprocess_runner_too(mac):
    """Even the real ask_person, on a Mac, can't start osascript unless a test replaces the runner."""
    assert confirm._run is not subprocess.run
    with pytest.raises(RuntimeError, match="approval window"):
        confirm._run([confirm.OSASCRIPT, "-e", "return 1"])
    assert real_ask_person("t", ["x"]) is False


def test_the_tests_never_use_the_real_lock_file():
    assert confirm.LOCK_PATH != confirm.DEFAULT_LOCK_PATH
    assert confirm.DEFAULT_LOCK_PATH == confirm.Path.home() / ".config" / "google-ads-mcp" / "approval-window.lock"


# ------------------------------------------------------------- one window across sessions (macOS)


def test_mac_refuses_while_another_session_holds_the_window(mac, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls))
    with confirm._one_window_at_a_time():  # what another Claude Code session's open window holds
        started = time.monotonic()
        with pytest.raises(WindowUnavailable) as raised:
            real_ask_person("t", ["x"])
        assert time.monotonic() - started < 1  # refused at once, never waits
    assert str(raised.value) == confirm.ANOTHER_WINDOW and calls == []
    assert real_ask_person("t", ["x"]) is True and len(calls) == 1  # free again once that window closed


def test_mac_the_lock_is_held_while_the_dialog_is_open_and_released_after(mac, monkeypatch):
    seen = []

    def run(args, **kwargs):
        with pytest.raises(WindowUnavailable, match="Another approval window"):
            with confirm._one_window_at_a_time():
                pass
        seen.append(True)
        return SimpleNamespace(returncode=0, stdout=APPROVE, stderr="")

    monkeypatch.setattr(confirm, "_run", run)
    assert real_ask_person("t", ["x"]) is True and real_ask_person("t", ["x"]) is True and seen == [True, True]


def test_mac_the_lock_works_across_processes(mac, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls))
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; from pathlib import Path; from gads_builder import confirm; "
         "confirm.LOCK_PATH = Path(sys.argv[1]); lock = confirm._one_window_at_a_time(); lock.__enter__(); "
         "print('held', flush=True); sys.stdin.read()", str(confirm.LOCK_PATH)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(WindowUnavailable) as raised:
            real_ask_person("t", ["x"])
        assert str(raised.value) == confirm.ANOTHER_WINDOW and calls == []
    finally:
        holder.stdin.close()
        holder.wait(30)
    assert real_ask_person("t", ["x"]) is True


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_mac_a_lock_path_that_is_a_symlink_is_refused(mac, monkeypatch, tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("")
    monkeypatch.setattr(confirm, "LOCK_PATH", tmp_path / "approval-window.lock")
    confirm.LOCK_PATH.symlink_to(target)
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls))
    with pytest.raises(WindowUnavailable, match="couldn't start on this Mac"):
        real_ask_person("t", ["x"])
    assert calls == []


def test_mac_the_lock_folder_is_made_private(mac, monkeypatch, tmp_path):
    monkeypatch.setattr(confirm, "LOCK_PATH", tmp_path / "new" / "approval-window.lock")
    monkeypatch.setattr(confirm, "_run", fake_run([]))
    assert real_ask_person("t", ["x"]) is True
    if not REALLY_WINDOWS:
        assert (tmp_path / "new").stat().st_mode & 0o777 == 0o700
        assert confirm.LOCK_PATH.stat().st_mode & 0o777 == 0o600


# ------------------------------------------------------------------------------------------ Windows

HOSTILE = [
    "Campaign: \"تست\" کمپین می\u200cخواهم (العربية)",
    "'; Start-Process calc; '",
    "\"; Start-Process calc; \"",
    "$(Start-Process calc) ${env:USERPROFILE} $env:SystemRoot @(1) @{a=1}",
    "`$x `n `r `0 back`tick` end`",
    "line one\nline two\r\n- image: \"C:\\Users\\x\\a.png\"",
    "%PATH% ^& calc | more > out.txt < in.txt !x! (y) --% -EncodedCommand AAAA",
    "@'\n'@ @\"\n\"@ #comment <# block #>",
    "\u202eevil\u2066 \u200b zero width \ufeff",
]


def test_windows_runs_one_fixed_command(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    assert real_ask_person("Approve?", ["Account: 1234567890", "Budget: 1.00"], timeout_s=60) is True
    [(args, kwargs)] = calls
    assert args == [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-Command", confirm.POWERSHELL_SCRIPT]
    assert kwargs["timeout"] == 90 and kwargs["capture_output"] is True and kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["creationflags"] == 0x08000000 and kwargs["text"] is True and kwargs["encoding"] == "utf-8"
    assert kwargs["cwd"] == SYSTEM32  # nothing is loaded from the folder Claude Code was started in
    env = kwargs["env"]
    assert base64.b64decode(env[confirm.ENV_TITLE]).decode("utf-8") == "Approve?"
    assert base64.b64decode(env[confirm.ENV_TEXT]).decode("utf-8") == "Account: 1234567890\nBudget: 1.00"
    assert env[confirm.ENV_SECONDS] == "60"
    assert env["SystemRoot"] == env["windir"] == WINDIR  # what Windows reports, not the planted SystemRoot


PLANTED = {
    "PATH": "C:\\Planted;C:\\Windows\\System32", "PSModulePath": "C:\\Planted\\Modules", "COR_ENABLE_PROFILING": "1",
    "COR_PROFILER": "{11111111-2222-3333-4444-555555555555}", "COR_PROFILER_PATH": "C:\\Planted\\profiler.dll",
    "COR_PROFILER_PATH_64": "C:\\Planted\\profiler64.dll", "CORECLR_ENABLE_PROFILING": "1",
    "__PSLockdownPolicy": "0", "PSExecutionPolicyPreference": "Bypass", "COMSPEC": "C:\\Planted\\cmd.exe",
    "windir": "C:\\Planted", "GADS_BUILDER_CONFIG": "C:\\Users\\YOU\\.config\\google-ads-mcp\\builder.toml",
    "GOOGLE_APPLICATION_CREDENTIALS": "C:\\Users\\YOU\\.config\\google-ads-mcp\\adc.json",
}
NEEDED = {
    "TEMP": "C:\\Users\\YOU\\AppData\\Local\\Temp", "TMP": "C:\\Users\\YOU\\AppData\\Local\\Temp",
    "USERPROFILE": "C:\\Users\\YOU", "LOCALAPPDATA": "C:\\Users\\YOU\\AppData\\Local",
    "APPDATA": "C:\\Users\\YOU\\AppData\\Roaming", "PATHEXT": ".COM;.EXE;.BAT;.CMD",
}


def test_windows_powershell_gets_a_minimal_environment():
    env = confirm.powershell_env("T", "x\ny", 60, SYSTEM32, WINDIR, base={**PLANTED, **NEEDED, "SystemRoot": "D:\\X"})
    assert env == {
        **NEEDED, "SystemRoot": WINDIR, "windir": WINDIR, "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
        confirm.ENV_TITLE: base64.b64encode(b"T").decode(), confirm.ENV_TEXT: base64.b64encode(b"x\ny").decode(),
        confirm.ENV_SECONDS: "60",
    }


def test_windows_environment_names_are_matched_without_regard_to_case():
    env = confirm.powershell_env("T", "x", 60, SYSTEM32, WINDIR,
                                 base={"temp": "C:\\T", "Tmp": "C:\\T2", "userprofile": "C:\\U", "PsModulePath": "x",
                                       "cor_enable_profiling": "1", "APPDATA": ""})
    assert {k: v for k, v in env.items() if not k.startswith("GADS_")} == {
        "TEMP": "C:\\T", "TMP": "C:\\T2", "USERPROFILE": "C:\\U", "SystemRoot": WINDIR, "windir": WINDIR,
        "COMSPEC": "C:\\Windows\\System32\\cmd.exe"}  # an empty value is left out


def test_windows_the_real_call_passes_only_that_environment(windows, monkeypatch):
    for name, value in {**PLANTED, **NEEDED}.items():
        monkeypatch.setenv(name, value)
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    assert real_ask_person("t", ["x"]) is True
    env = calls[0][1]["env"]
    assert set(env) == {*NEEDED, "SystemRoot", "windir", "COMSPEC", confirm.ENV_TITLE, confirm.ENV_TEXT,
                        confirm.ENV_SECONDS}
    assert not [v for v in env.values() if "Planted" in v]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_windows_never_puts_the_title_or_text_in_the_command(windows, monkeypatch, hostile):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    lines = ["Account: 1234567890", f"Xq7Zt {hostile}", f"- {confirm.quoted(hostile)}"]
    assert real_ask_person(f"Approve Xq7Zt {hostile}", lines) is True
    [(args, kwargs)] = calls
    assert args == confirm.powershell_command(POWERSHELL)  # the same command, whatever the text
    shown = confirm.dialog_text(lines).split("\n") + [confirm._flat_line(f"Approve Xq7Zt {hostile}")]
    for arg in args:
        assert "1234567890" not in arg and "Xq7Zt" not in arg and not [line for line in shown if line in arg], arg
    env = kwargs["env"]
    for name in (confirm.ENV_TITLE, confirm.ENV_TEXT):
        assert re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", env[name])  # base64: nothing PowerShell could run
    assert base64.b64decode(env[confirm.ENV_TEXT]).decode("utf-8") == confirm.dialog_text(lines)
    assert base64.b64decode(env[confirm.ENV_TITLE]).decode("utf-8") == confirm._flat_line(f"Approve Xq7Zt {hostile}")
    assert "\n" not in base64.b64decode(env[confirm.ENV_TITLE]).decode("utf-8")
    assert not [v for k, v in env.items() if k not in (confirm.ENV_TITLE, confirm.ENV_TEXT) and "Xq7Zt" in v]


def test_the_windows_script_is_fixed_and_safe_by_design():
    script = confirm.POWERSHELL_SCRIPT
    assert '"' not in script and "\\" not in script and "\n" not in script and "\r" not in script  # nothing to escape
    assert script.isascii() and len(script) < 24000  # far inside the 32,767-character command line
    for part in [
        "$form.TopMost = $true", "$box.Multiline = $true", "$box.ReadOnly = $true", "$box.ScrollBars = 'Both'",
        "$box.WordWrap = $false",  # each line is one line on screen, never wrapped into what looks like two
        f"FromBase64String($env:{confirm.ENV_TITLE})", f"FromBase64String($env:{confirm.ENV_TEXT})",
        "$approve.Text = 'Approve'", "$cancel.Text = 'Cancel'",
        "$form.AcceptButton = $cancel", "$form.CancelButton = $cancel", "$form.ActiveControl = $cancel",
        "$cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel",
        "$approve.DialogResult = [Windows.Forms.DialogResult]::None", "$approve.TabStop = $false",
        "$timer.Interval = $seconds * 1000", "$timer.Start()",
        # the guard: fixed C# carried as base64 in the script itself, compiled before anything is shown
        f"$guardSource = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{confirm.GUARD_B64}'))",
        "Add-Type -TypeDefinition $guardSource -ReferencedAssemblies System.Windows.Forms, System.Drawing",
        "$owner = [GadsApprovalGuard]::TakeWindowLock()",
        f"[GadsApprovalGuard]::Start($approve, {confirm.ARM_MS})",
        "[GadsApprovalGuard]::Stop()",
        # any failure before the window shows: its own answer, never a No
        f"catch {{ 'UNAVAILABLE ' + $_.Exception.Message; exit {confirm.EXIT_UNAVAILABLE} }}",
        f"if (-not $owner) {{ [Console]::Out.Write('BUSY'); exit {confirm.EXIT_BUSY} }}",
    ]:
        assert part in script, part
    assert script.startswith("$ErrorActionPreference = 'Stop'; try { Add-Type -AssemblyName System.Windows.Forms; ")
    assert confirm.ARM_MS == 1500 and confirm.EXIT_UNAVAILABLE == 3 and confirm.EXIT_BUSY == 4
    # Only a left mouse click on Approve that the guard accepts sets OK; a key press never raises MouseClick.
    assert script.count("[Windows.Forms.DialogResult]::OK") == 2
    assert ("$approve.Add_MouseClick({ if ($_.Button -eq [Windows.Forms.MouseButtons]::Left) { "
            "$why = [GadsApprovalGuard]::Check(); if ($why -eq '') { $form.Tag = 'approved'; "
            "$form.DialogResult = [Windows.Forms.DialogResult]::OK } else { "
            "[Console]::Error.WriteLine('refused click: ' + $why) } } })") in script
    assert ("$timer.Add_Tick({ $timer.Stop(); $form.Tag = $null; "
            "$form.DialogResult = [Windows.Forms.DialogResult]::Cancel })") in script
    assert script.count("APPROVED") == 1 and script.endswith(
        "if ($result -eq [Windows.Forms.DialogResult]::OK -and $form.Tag -eq 'approved') "
        "{ [Console]::Out.Write('APPROVED') } else { [Console]::Out.Write('DECLINED') }")
    # The guard and the one-window lock are in place before the form exists, and the form shows only after.
    try_block, _, after = script.partition(" } catch { ")
    assert "ShowDialog" not in try_block and "$result = $form.ShowDialog()" in after
    assert try_block.index("TakeWindowLock") < try_block.index("New-Object Windows.Forms.Form")
    assert try_block.index("[GadsApprovalGuard]::Start(") > try_block.index("$approve.Add_MouseClick(")


def test_the_guard_is_fixed_csharp_that_never_holds_plan_text():
    source = confirm.GUARD_SOURCE
    assert base64.b64decode(confirm.GUARD_B64).decode("ascii") == source and source.isascii()
    assert confirm.GUARD_B64 in confirm.POWERSHELL_SCRIPT
    for part in [
        "public static class GadsApprovalGuard",
        "public const int ClickWindowMs = 300;",
        "const int WH_MOUSE_LL = 14;", "const int WM_LBUTTONDOWN = 0x0201;", "const int WM_LBUTTONUP = 0x0202;",
        "const uint LLMHF_INJECTED = 0x00000001;", "const uint LLMHF_LOWER_IL_INJECTED = 0x00000002;",
        'new Mutex(false, @"Local\\GadsBuilderApproval")', "windowLock.WaitOne(0, false)",
        "hook = SetWindowsHookExW(WH_MOUSE_LL, HookProc, GetModuleHandleW(null), 0);",
        "bool injected = (info.Flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED)) != 0;",
        "bool onApprove = !injected && armed && IsOnApprove(info.Pt);",
        "upOnApprove = onApprove && downOnApprove;",
        "return CallNextHookEx(hook, nCode, wParam, lParam);",
        'if (!armed) return "Approve was not armed yet";',
        'if (lastInjected) return "injected input";',
        'if (!clicked) return "no physical click on Approve";',
        "if (age < 0 || age > ClickWindowMs)",
        "approve.Enabled = false;",
    ]:
        assert part in source, part
    # It reads nothing from outside and approves nothing itself: the script decides, from Check()'s answer.
    for absent in ["GetEnvironmentVariable", "DialogResult", "APPROVED", "Process", "SendInput", "PostMessage",
                   "SendMessage", "File.", "Console"]:
        assert absent not in source, absent
    assert source.count('return "";') == 1  # Check() says yes in one place only


@pytest.mark.parametrize("returncode, stdout, expected", [
    (0, "APPROVED", True),
    (0, "APPROVED\r\n", True),
    (0, "  APPROVED \n", True),
    (0, "DECLINED", False),  # Cancel, Enter, Esc, closed or timed out
    (0, "DECLINED\r\n", False),
])
def test_windows_approve_or_no(windows, monkeypatch, returncode, stdout, expected):
    monkeypatch.setattr(confirm, "_run", fake_run([], returncode=returncode, stdout=stdout))
    assert real_ask_person("t", ["x"]) is expected
    assert confirm.windows_answer(returncode, stdout) is expected
    assert confirm.windows_approved(returncode, stdout) is expected


@pytest.mark.parametrize("returncode, stdout, message", [
    (4, "BUSY", confirm.ANOTHER_WINDOW),
    (3, "UNAVAILABLE Cannot add type. Definition of new types is not supported in this language mode.\r\n",
     "The approval window couldn't start on this PC: Cannot add type. Definition of new types is not supported in "
     "this language mode. Nothing was changed."),
    (3, "UNAVAILABLE the mouse check could not start (error 5)",
     "The approval window couldn't start on this PC: the mouse check could not start (error 5). Nothing was changed."),
    (3, "UNAVAILABLE", "The approval window couldn't start on this PC: PowerShell gave no reason. Nothing was changed."),
    (3, "UNAVAILABLE x\r\nline two " + "y" * 300, "The approval window couldn't start on this PC: x line two y"),
    (1, "", "The approval window couldn't start on this PC: PowerShell ended with exit code 1 and no answer."),
    (0, "", "PowerShell ended with exit code 0 and no answer"),
    (0, "approved", "exit code 0"),
    (0, "APPROVED\r\nAPPROVED", "exit code 0"),
    (0, "APPROVEDX", "exit code 0"),
    (0, None, "exit code 0"),
    (0, "BUSY", "exit code 0"),
    (1, "APPROVED", "exit code 1"),  # a PowerShell error
    (-1, "APPROVED", "exit code -1"),
    (3, "BUSY", "exit code 3"),
    (4, "UNAVAILABLE x", "exit code 4"),
    (2, "", "exit code 2"),
])
def test_windows_anything_else_means_no_window_and_is_not_a_no(windows, monkeypatch, returncode, stdout, message):
    monkeypatch.setattr(confirm, "_run", fake_run([], returncode=returncode, stdout=stdout))
    with pytest.raises(WindowUnavailable) as raised:
        real_ask_person("t", ["x"])
    said = str(raised.value)
    assert message in said and said.endswith("Nothing was changed.") and len(said) < 300
    assert said == confirm.ANOTHER_WINDOW or said.startswith("The approval window couldn't start on this PC: ")
    assert confirm.windows_approved(returncode, stdout) is False


@pytest.mark.parametrize("error", [FileNotFoundError("no powershell"), PermissionError("blocked by group policy"),
                                   OSError("boom")])
def test_windows_a_powershell_that_cannot_start_is_unavailable(windows, monkeypatch, error):
    monkeypatch.setattr(confirm, "_run", fake_run([], raises=error))
    with pytest.raises(WindowUnavailable, match=r"^The approval window couldn't start on this PC: "):
        real_ask_person("t", ["x"])


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("powershell.exe", 90),
                                   UnicodeDecodeError("utf-8", b"", 0, 1, "x"), RuntimeError("anything")])
def test_windows_other_failures_are_a_no(windows, monkeypatch, error):
    monkeypatch.setattr(confirm, "_run", fake_run([], raises=error))
    assert real_ask_person("t", ["x"]) is False


def test_windows_timeouts_are_clamped_and_outlast_the_window(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    for asked in (1, 60, 99999):
        real_ask_person("t", ["x"], timeout_s=asked)
    assert [c[1]["env"][confirm.ENV_SECONDS] for c in calls] == ["10", "60", "3600"]
    assert [c[1]["timeout"] for c in calls] == [40, 90, 3630]  # the process gets 30 seconds more than the window


@pytest.mark.parametrize("folders", [
    None, ("", ""), ("System32", "Windows"), ("\\Windows\\System32", "\\Windows"),
    ("C:Windows\\System32", "C:Windows"), ("\\\\server\\share\\Windows\\System32", "\\\\server\\share\\Windows"),
    ("\\\\?\\C:\\Windows\\System32", "\\\\?\\C:\\Windows"), (SYSTEM32, ""), ("", WINDIR), (SYSTEM32, "C:"),
    ("C:\\Windows\\System32:x", WINDIR),
])
def test_windows_without_usable_system_folders_is_unavailable_and_runs_nothing(windows, monkeypatch, folders):
    monkeypatch.setattr(confirm, "_windows_folders", lambda: folders)
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    assert confirm.windows_folders() is None
    with pytest.raises(WindowUnavailable, match="couldn't start on this PC: Windows did not report its System32"):
        real_ask_person("t", ["x"])
    assert calls == []


def test_windows_powershell_is_found_by_its_absolute_path_under_system32():
    assert confirm.powershell_path("D:\\WINNT\\System32\\") == "D:\\WINNT\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"


def test_windows_system32_comes_from_windows_itself():
    folders = confirm._windows_folders()
    if not REALLY_WINDOWS:
        assert folders is None  # no Windows API to ask
        return
    system32, windir = folders
    assert ntpath.normcase(system32) == ntpath.normcase(ntpath.join(windir, "System32"))
    assert confirm.windows_folders() == (system32, windir) and os.path.isfile(confirm.powershell_path(system32))


@pytest.mark.skipif(not REALLY_WINDOWS, reason="starts a real (missing) program with Windows-only flags")
def test_windows_a_missing_powershell_is_unavailable(windows, monkeypatch, tmp_path):
    folder = str(tmp_path)
    monkeypatch.setattr(confirm, "_windows_folders", lambda: (folder + "\\System32", folder))
    monkeypatch.setattr(confirm, "_run", subprocess.run)  # the real runner, put back on purpose: no powershell.exe
    with pytest.raises(WindowUnavailable, match="couldn't start on this PC"):
        real_ask_person("t", ["x"])


def test_windows_a_list_too_long_for_the_window_opens_nothing(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    with pytest.raises(WindowUnavailable, match="split the plan"):
        real_ask_person("t", ["x"] * 46)
    assert calls == []


def test_windows_the_guard_stops_the_subprocess_runner_too(windows):
    assert confirm._run is not subprocess.run
    assert real_ask_person("t", ["x"]) is False


# ------------------------------------------------------------------------- Windows only, for real

ON_WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows PowerShell")
REAL_WINDOW = pytest.mark.skipif(sys.platform != "win32" or os.environ.get("GADS_WINDOW_TEST") != "1",
                                 reason="opens real windows for a few seconds; set GADS_WINDOW_TEST=1 on Windows (CI does)")


def _powershell() -> str:
    return confirm.powershell_path(confirm.windows_folders()[0])


def _powershell_says(command: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
                          env={**os.environ, **env}, capture_output=True, text=True, timeout=180,
                          stdin=subprocess.DEVNULL)


@ON_WINDOWS
def test_windows_powershell_parses_the_script():
    check = ("$tokens = $null; $errors = $null; "
             "[void][System.Management.Automation.Language.Parser]::ParseInput($env:GADS_TEST_SCRIPT, [ref]$tokens, "
             "[ref]$errors); [Console]::Out.Write($errors.Count)")
    proc = _powershell_says(check, GADS_TEST_SCRIPT=confirm.POWERSHELL_SCRIPT)
    assert proc.returncode == 0 and proc.stdout.strip() == "0", proc.stderr


@ON_WINDOWS
def test_windows_the_guard_compiles():
    proc = _powershell_says("$ErrorActionPreference = 'Stop'; Add-Type -AssemblyName System.Windows.Forms; "
                            "Add-Type -TypeDefinition $env:GADS_TEST_SOURCE -ReferencedAssemblies "
                            "System.Windows.Forms, System.Drawing; [Console]::Out.Write([GadsApprovalGuard]::ClickWindowMs)",
                            GADS_TEST_SOURCE=confirm.GUARD_SOURCE)
    assert proc.returncode == 0 and proc.stdout.strip() == "300", proc.stderr


def _start_window(title: str, text: str, seconds: int) -> subprocess.Popen:
    """The approval window exactly as the builder starts it: same command, environment and folder."""
    launch = confirm.windows_launch(title, text, seconds)
    return subprocess.Popen(launch["args"], env=launch["env"], cwd=launch["cwd"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", creationflags=confirm.CREATE_NO_WINDOW)


# A test-only attacker: finds the window's Approve button, posts clicks to it (BM_CLICK and
# WM_LBUTTONDOWN/UP, before and after it is armed), then moves the cursor onto it and clicks with SendInput,
# which marks the input as injected. None of it may approve.
ATTACKER = r"""
param([string]$Title, [string]$Mode)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
#pragma warning disable 649, 169
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class GadsTestAttacker
{
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public MOUSEINPUT mi; }
    public delegate bool EnumProc(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindWindowW(string cls, string title);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, EnumProc proc, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowTextW(IntPtr hwnd, StringBuilder text, int max);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] static extern bool PostMessageW(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll", SetLastError = true)] static extern uint SendInput(uint count, INPUT[] inputs, int size);
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr hwnd);

    const uint BM_CLICK = 0x00F5, WM_LBUTTONDOWN = 0x0201, WM_LBUTTONUP = 0x0202, MOUSEEVENTF_LEFTDOWN = 0x0002, MOUSEEVENTF_LEFTUP = 0x0004, WM_CLOSE = 0x0010;

    public static bool Close(string title)
    {
        IntPtr form = FindWindowW(null, title);
        return form != IntPtr.Zero && PostMessageW(form, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
    }

    public static IntPtr FindApprove(string title, int waitMs)
    {
        for (int waited = 0; waited < waitMs; waited += 100)
        {
            IntPtr form = FindWindowW(null, title);
            IntPtr found = IntPtr.Zero;
            if (form != IntPtr.Zero)
            {
                EnumChildWindows(form, delegate (IntPtr child, IntPtr unused)
                {
                    StringBuilder text = new StringBuilder(64);
                    GetWindowTextW(child, text, 64);
                    if (text.ToString() == "Approve") { found = child; return false; }
                    return true;
                }, IntPtr.Zero);
            }
            if (found != IntPtr.Zero) return found;
            Thread.Sleep(100);
        }
        return IntPtr.Zero;
    }

    public static string Attack(IntPtr approve)
    {
        StringBuilder log = new StringBuilder();
        IntPtr at = new IntPtr((8 << 16) | 8);
        log.Append("early-enabled=" + IsWindowEnabled(approve));
        log.Append(" early-bm-click=" + PostMessageW(approve, BM_CLICK, IntPtr.Zero, IntPtr.Zero));
        log.Append(" early-posted=" + (PostMessageW(approve, WM_LBUTTONDOWN, new IntPtr(1), at) && PostMessageW(approve, WM_LBUTTONUP, IntPtr.Zero, at)));
        Thread.Sleep(3000);
        log.Append(" armed-enabled=" + IsWindowEnabled(approve));
        log.Append(" posted=" + (PostMessageW(approve, WM_LBUTTONDOWN, new IntPtr(1), at) && PostMessageW(approve, WM_LBUTTONUP, IntPtr.Zero, at)));
        Thread.Sleep(500);
        log.Append(" bm-click=" + PostMessageW(approve, BM_CLICK, IntPtr.Zero, IntPtr.Zero));
        Thread.Sleep(500);
        RECT r;
        GetWindowRect(approve, out r);
        log.Append(" cursor=" + SetCursorPos((r.Left + r.Right) / 2, (r.Top + r.Bottom) / 2));
        INPUT[] click = new INPUT[2];
        click[0].type = 0; click[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
        click[1].type = 0; click[1].mi.dwFlags = MOUSEEVENTF_LEFTUP;
        uint sent = SendInput(2, click, Marshal.SizeOf(typeof(INPUT)));
        log.Append(" sendinput=" + sent + " error=" + Marshal.GetLastWin32Error());
        Thread.Sleep(800);
        return log.ToString();
    }
}
'@
$approve = [GadsTestAttacker]::FindApprove($Title, 60000)
if ($approve -eq [IntPtr]::Zero) { [Console]::Out.Write('NOT FOUND'); exit 2 }
if ($Mode -eq 'find') { [Console]::Out.Write('FOUND'); exit 0 }
if ($Mode -eq 'close') { [Console]::Out.Write([GadsTestAttacker]::Close($Title)); exit 0 }
[Console]::Out.Write([GadsTestAttacker]::Attack($approve))
"""


def _attacker(tmp_path, title: str, mode: str) -> subprocess.CompletedProcess:
    script = tmp_path / "attacker.ps1"
    script.write_text(ATTACKER, encoding="utf-8-sig")
    return subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-STA",
                           "-File", str(script), "-Title", title, "-Mode", mode],
                          capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL,
                          creationflags=confirm.CREATE_NO_WINDOW)


@REAL_WINDOW
def test_windows_the_real_window_opens_with_hostile_text_and_times_out_as_a_no(tmp_path):
    marker = tmp_path / "pwned.txt"
    lines = HOSTILE + [f"'; New-Item -ItemType File -Path '{marker}'; '", f"$(New-Item -ItemType File -Path '{marker}')"]
    window = _start_window(confirm._flat_line(HOSTILE[0]), confirm.dialog_text(lines), 3)
    out, err = window.communicate(timeout=120)
    assert out.strip() != "APPROVED"
    assert (window.returncode, out) == (0, "DECLINED"), err  # the timer closed it as Cancel
    assert confirm.windows_answer(window.returncode, out) is False
    assert not marker.exists()  # no text ran as a command


@REAL_WINDOW
def test_windows_injected_and_posted_clicks_never_approve(tmp_path):
    title = f"Gads window test {uuid.uuid4().hex[:8]}"
    window = _start_window(title, "Nothing is sent. A test program clicks Approve with fake input.", 10)
    try:
        attack = _attacker(tmp_path, title, "attack")
    finally:
        out, err = window.communicate(timeout=120)
    report = f"attacker: {attack.stdout} {attack.stderr} | window: {out!r} {err}"
    assert attack.returncode == 0, report
    assert "early-enabled=False" in attack.stdout and "armed-enabled=True" in attack.stdout, report  # armed after 1.5 s
    assert "posted=True" in attack.stdout and "bm-click=True" in attack.stdout, report
    assert "cursor=True" in attack.stdout and "sendinput=2 " in attack.stdout, report  # Windows took the fake click
    assert (window.returncode, out) == (0, "DECLINED"), report  # ...and the window still timed out as a No
    # The fake clicks reached Approve's MouseClick, and the guard refused each one.
    assert "refused click: no physical click on Approve" in err, report
    assert "refused click: injected input" in err, report


@REAL_WINDOW
def test_windows_a_second_window_is_refused_while_one_is_open(tmp_path):
    title = f"Gads window test {uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    first = _start_window(title, "The first window.", 120)  # closed below, long before its own timer
    try:
        found = _attacker(tmp_path, title, "find")
        found_at = time.monotonic() - started
        second = _start_window(title + " (second)", "The second window must not open.", 10)
        out2, err2 = second.communicate(timeout=120)
        second_at = time.monotonic() - started
    finally:
        closed = _attacker(tmp_path, title, "close")  # what the window's close button does: a No
        out1, err1 = first.communicate(timeout=120)
    report = f"found at {found_at:.1f}s, second done at {second_at:.1f}s; second: {out2!r} {err2}; first: {err1}"
    assert found.stdout == "FOUND", found.stderr
    assert (second.returncode, out2) == (4, "BUSY"), report
    with pytest.raises(WindowUnavailable) as raised:
        confirm.windows_answer(second.returncode, out2)
    assert str(raised.value) == confirm.ANOTHER_WINDOW
    assert closed.stdout == "True" and (first.returncode, out1) == (0, "DECLINED"), (closed.stdout, closed.stderr, err1)


@REAL_WINDOW
def test_windows_locked_down_powershell_is_unavailable_not_a_no():
    """Constrained Language Mode (what AppLocker or Device Guard impose) can't run Add-Type: the script says so."""
    launch = confirm.windows_launch("Gads window test", "Must not open.", 3)
    args = launch["args"][:-1] + ["$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'; "
                                  + launch["args"][-1]]
    proc = subprocess.run(args, env=launch["env"], cwd=launch["cwd"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, stdin=subprocess.DEVNULL,
                          creationflags=confirm.CREATE_NO_WINDOW)
    assert proc.returncode == confirm.EXIT_UNAVAILABLE and proc.stdout.startswith("UNAVAILABLE"), (proc.stdout, proc.stderr)
    with pytest.raises(WindowUnavailable, match=r"^The approval window couldn't start on this PC: "):
        confirm.windows_answer(proc.returncode, proc.stdout)
