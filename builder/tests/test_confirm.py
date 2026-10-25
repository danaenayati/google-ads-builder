import base64
import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gads_builder import confirm
from gads_builder.confirm import ask_person as real_ask_person  # the autouse guard replaces confirm.ask_person
from gads_builder.confirm import window_supported as real_window_supported  # and window_supported

APPROVE = "button returned:Approve, gave up:false\n"
WIN_APPROVE = "APPROVED\r\n"
SYSTEM_ROOT = "C:\\Windows"
POWERSHELL = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"


def fake_run(calls, *, returncode=0, stdout=APPROVE, raises=None):
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    return run


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(confirm.sys, "platform", "darwin")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(confirm.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", SYSTEM_ROOT)


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


@pytest.mark.parametrize("returncode, stdout, expected", [
    (0, "button returned:Approve, gave up:false\n", True),
    (0, "button returned:, gave up:true\n", False),  # timed out
    (0, "button returned:Approve, gave up:true\n", False),
    (1, "", False),  # Cancel: "execution error: User canceled. (-128)"
    (0, "button returned:Cancel, gave up:false\n", False),
    (0, "", False),
    (0, "button returned:Approved, gave up:false\n", False),
    (1, "button returned:Approve, gave up:false\n", False),
])
def test_only_a_clear_approve_counts(mac, monkeypatch, returncode, stdout, expected):
    monkeypatch.setattr(confirm, "_run", fake_run([], returncode=returncode, stdout=stdout))
    assert real_ask_person("t", ["x"]) is expected


@pytest.mark.parametrize("error", [FileNotFoundError("no osascript"), subprocess.TimeoutExpired("osascript", 330),
                                   OSError("boom"), RuntimeError("anything")])
def test_any_failure_is_a_no(mac, monkeypatch, error):
    monkeypatch.setattr(confirm, "_run", fake_run([], raises=error))
    assert real_ask_person("t", ["x"]) is False


def test_a_missing_osascript_is_a_no(mac, monkeypatch, tmp_path):
    monkeypatch.setattr(confirm, "OSASCRIPT", str(tmp_path / "no-such-osascript"))
    monkeypatch.setattr(confirm, "_run", subprocess.run)  # the real runner, put back on purpose: no osascript
    assert real_ask_person("t", ["x"]) is False  # subprocess.run raises FileNotFoundError


@pytest.mark.parametrize("platform", ["linux", "freebsd14", "cygwin", "emscripten"])
def test_any_other_system_is_a_no_and_runs_nothing(monkeypatch, platform):
    calls = []
    monkeypatch.setattr(confirm.sys, "platform", platform)
    monkeypatch.setenv("SystemRoot", SYSTEM_ROOT)
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    assert real_ask_person("t", ["x"]) is False and calls == []
    assert real_window_supported() is False
    assert "only on macOS and Windows" in confirm.UNSUPPORTED and "Nothing was changed" in confirm.UNSUPPORTED


@pytest.mark.parametrize("platform, program", [("darwin", "/usr/bin/osascript"), ("win32", POWERSHELL)])
def test_macos_and_windows_each_open_their_own_window(monkeypatch, platform, program):
    calls = []
    monkeypatch.setattr(confirm.sys, "platform", platform)
    monkeypatch.setenv("SystemRoot", SYSTEM_ROOT)
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


def test_a_too_long_list_is_a_no_and_opens_nothing(mac, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls))
    assert real_ask_person("t", ["x"] * 46) is False and calls == []


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
    env = kwargs["env"]
    assert base64.b64decode(env[confirm.ENV_TITLE]).decode("utf-8") == "Approve?"
    assert base64.b64decode(env[confirm.ENV_TEXT]).decode("utf-8") == "Account: 1234567890\nBudget: 1.00"
    assert env[confirm.ENV_SECONDS] == "60"
    assert {k.upper(): v for k, v in env.items()}["SYSTEMROOT"] == SYSTEM_ROOT  # the rest is inherited


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


def test_the_windows_script_is_fixed_and_safe_by_design():
    script = confirm.POWERSHELL_SCRIPT
    assert '"' not in script and "\\" not in script and "\n" not in script  # nothing to escape on the command line
    for part in [
        "$form.TopMost = $true", "$box.Multiline = $true", "$box.ReadOnly = $true", "$box.ScrollBars = 'Both'",
        "$box.WordWrap = $false",  # each line is one line on screen, never wrapped into what looks like two
        f"FromBase64String($env:{confirm.ENV_TITLE})", f"FromBase64String($env:{confirm.ENV_TEXT})",
        "$approve.Text = 'Approve'", "$cancel.Text = 'Cancel'",
        "$form.AcceptButton = $cancel", "$form.CancelButton = $cancel", "$form.ActiveControl = $cancel",
        "$cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel",
        "$approve.DialogResult = [Windows.Forms.DialogResult]::None", "$approve.TabStop = $false",
        "$timer.Interval = $seconds * 1000", "$timer.Start()",
    ]:
        assert part in script, part
    # Only a left mouse click on Approve sets OK; a key press never raises MouseClick.
    assert script.count("[Windows.Forms.DialogResult]::OK") == 2
    assert ("$approve.Add_MouseClick({ if ($_.Button -eq [Windows.Forms.MouseButtons]::Left) { $form.Tag = 'approved'; "
            "$form.DialogResult = [Windows.Forms.DialogResult]::OK } })") in script
    assert ("$timer.Add_Tick({ $timer.Stop(); $form.Tag = $null; "
            "$form.DialogResult = [Windows.Forms.DialogResult]::Cancel })") in script
    assert script.count("APPROVED") == 1 and script.endswith(
        "if ($result -eq [Windows.Forms.DialogResult]::OK -and $form.Tag -eq 'approved') "
        "{ [Console]::Out.Write('APPROVED') }")


@pytest.mark.parametrize("returncode, stdout, expected", [
    (0, "APPROVED", True),
    (0, "APPROVED\r\n", True),
    (0, "  APPROVED \n", True),
    (0, "", False),  # Cancel, Enter, Esc, closed or timed out
    (0, "approved", False),
    (0, "APPROVED\r\nAPPROVED", False),
    (0, "APPROVEDX", False),
    (0, "Cancel", False),
    (0, None, False),
    (1, "APPROVED", False),  # a PowerShell error
    (-1, "APPROVED", False),
    (2, "", False),
])
def test_windows_only_a_clear_approve_counts(windows, monkeypatch, returncode, stdout, expected):
    monkeypatch.setattr(confirm, "_run", fake_run([], returncode=returncode, stdout=stdout))
    assert real_ask_person("t", ["x"]) is expected
    assert confirm.windows_approved(returncode, stdout) is expected


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("powershell.exe", 90), FileNotFoundError("no powershell"),
                                   PermissionError("blocked"), OSError("boom"), UnicodeDecodeError("utf-8", b"", 0, 1, "x"),
                                   RuntimeError("anything")])
def test_windows_any_failure_is_a_no(windows, monkeypatch, error):
    monkeypatch.setattr(confirm, "_run", fake_run([], raises=error))
    assert real_ask_person("t", ["x"]) is False


def test_windows_timeouts_are_clamped_and_outlast_the_window(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    for asked in (1, 60, 99999):
        real_ask_person("t", ["x"], timeout_s=asked)
    assert [c[1]["env"][confirm.ENV_SECONDS] for c in calls] == ["10", "60", "3600"]
    assert [c[1]["timeout"] for c in calls] == [40, 90, 3630]  # the process gets 30 seconds more than the window


@pytest.mark.parametrize("root", [None, "", "Windows", "\\Windows", "C:", "C:Windows", "\\\\server\\share\\Windows"])
def test_windows_without_a_usable_system_root_is_a_no_and_runs_nothing(windows, monkeypatch, root):
    if root is None:
        monkeypatch.delenv("SystemRoot")
    else:
        monkeypatch.setenv("SystemRoot", root)
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    assert confirm.powershell_path() is None
    assert real_ask_person("t", ["x"]) is False and calls == []


def test_windows_powershell_is_found_by_its_absolute_path(monkeypatch):
    monkeypatch.setenv("SystemRoot", "D:\\WINNT\\")
    assert confirm.powershell_path() == "D:\\WINNT\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"


def test_windows_a_missing_powershell_is_a_no(windows, monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", "Z:\\no-such-windows" if sys.platform != "win32" else str(tmp_path))
    monkeypatch.setattr(confirm, "_run", subprocess.run)  # the real runner, put back on purpose: no powershell.exe
    assert real_ask_person("t", ["x"]) is False


def test_windows_a_list_too_long_for_the_window_is_a_no_and_opens_nothing(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(confirm, "_run", fake_run(calls, stdout=WIN_APPROVE))
    assert real_ask_person("t", ["x"] * 46) is False and calls == []


def test_windows_the_guard_stops_the_subprocess_runner_too(windows):
    assert confirm._run is not subprocess.run
    assert real_ask_person("t", ["x"]) is False


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows PowerShell")
def test_windows_powershell_parses_the_script():
    check = ("$tokens = $null; $errors = $null; "
             "[void][System.Management.Automation.Language.Parser]::ParseInput($env:GADS_TEST_SCRIPT, [ref]$tokens, "
             "[ref]$errors); [Console]::Out.Write($errors.Count)")
    proc = subprocess.run([confirm.powershell_path(), "-NoProfile", "-NonInteractive", "-Command", check],
                          env={**os.environ, "GADS_TEST_SCRIPT": confirm.POWERSHELL_SCRIPT}, capture_output=True,
                          text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0 and proc.stdout.strip() == "0", proc.stderr


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("GADS_WINDOW_TEST") != "1",
                    reason="opens a real window for 3 seconds; set GADS_WINDOW_TEST=1 on Windows (CI does)")
def test_windows_the_real_window_opens_with_hostile_text_and_times_out_as_cancel(tmp_path):
    marker = tmp_path / "pwned.txt"
    lines = HOSTILE + [f"'; New-Item -ItemType File -Path '{marker}'; '", f"$(New-Item -ItemType File -Path '{marker}')"]
    proc = subprocess.run(confirm.powershell_command(confirm.powershell_path()),
                          env=confirm.powershell_env(confirm._flat_line(HOSTILE[0]), confirm.dialog_text(lines), 3),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                          stdin=subprocess.DEVNULL, creationflags=confirm.CREATE_NO_WINDOW)
    assert proc.stdout.strip() != "APPROVED"
    assert (proc.returncode, proc.stdout) == (0, ""), proc.stderr  # the timer closed it as Cancel
    assert not marker.exists()  # no text ran as a command
