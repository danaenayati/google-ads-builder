import dataclasses
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from gads_builder import images, rules


@pytest.mark.parametrize("shape, size", [("landscape", (1200, 628)), ("square", (1200, 1200)), ("portrait", (1200, 1500))])
def test_fit_produces_exact_google_sizes(make_png, tmp_path, shape, size):
    out = images.fit(make_png(1536, 1024), shape, tmp_path / f"{shape}.jpg")
    with Image.open(out) as im:
        assert im.size == size and im.format == "JPEG"
    assert rules.classify_image(out)[0] == shape


THREAD = "019a-test-thread"


@pytest.fixture(autouse=True)
def _codex_installed(cfg):
    """The fake Codex program exists (generate checks that before anything else); `run` is always faked."""
    cfg.codex_path.write_bytes(b"")


def _started(thread=THREAD):
    return json.dumps({"type": "thread.started", "thread_id": thread}) + "\n" + '{"type":"turn.completed"}\n'


def _make_codex_image(cfg, thread=THREAD, name="exec-1.png", mtime=None):
    target = cfg.codex_images_dir / thread / name
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1536, 1024), (10, 120, 200)).save(target)
    if mtime:
        os.utime(target, (mtime, mtime))
    return target


def test_generate_uses_only_its_own_thread_folder(cfg):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        _make_codex_image(cfg)
        # a NEWER image from some other run must be ignored
        _make_codex_image(cfg, "other-thread", "newer.png", mtime=time.time() + 100)
        return SimpleNamespace(returncode=0, stdout=_started(), stderr="")

    result = images.generate("a calm street at sunset", "landscape", "Test Image", cfg, run=fake_run)
    cmd, kwargs = calls[0]
    assert cmd == [str(cfg.codex_path), "exec", "--json", "--skip-git-repo-check", "-s", "read-only",
                   "-m", "test-model", "-c", 'model_reasoning_effort="low"', "-"]
    assert "a calm street at sunset" in kwargs["input"] and "stdin" not in kwargs  # the prompt goes on stdin
    assert kwargs["encoding"] == "utf-8" and kwargs["text"] is True
    folder = Path(result["image"]).parent
    assert kwargs["cwd"] == str(folder)
    assert "centred" in (folder / "prompt.txt").read_text(encoding="utf-8")
    assert "a calm street at sunset" in (folder / "prompt.txt").read_text(encoding="utf-8")
    with Image.open(result["image"]) as im:
        assert im.size == (1200, 628)
    with Image.open(result["original"]) as im:
        assert im.size == (1536, 1024)  # the thread image, not the newer other one
    assert result["shape"] == "landscape"


def test_generate_reports_a_nonzero_exit(cfg):
    fake_run = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="some out", stderr="model not supported")  # noqa: E731
    with pytest.raises(images.ImageGenerationError, match="model not supported") as e:
        images.generate("x", "square", "Nothing", cfg, run=fake_run)
    assert "some out" in str(e.value)
    assert not any(Path(cfg.images_dir).iterdir())  # the empty folder was removed


def test_generate_needs_a_thread_id(cfg):
    _make_codex_image(cfg)
    fake_run = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="no json here\n", stderr="")  # noqa: E731
    with pytest.raises(images.ImageGenerationError, match="thread"):
        images.generate("x", "square", "n", cfg, run=fake_run)


def test_generate_without_an_image_in_the_thread_folder(cfg):
    fake_run = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=_started(), stderr="")  # noqa: E731
    with pytest.raises(images.ImageGenerationError, match="no image"):
        images.generate("x", "square", "n", cfg, run=fake_run)
    assert not any(Path(cfg.images_dir).iterdir())


@pytest.mark.parametrize("exc", [
    subprocess.TimeoutExpired("codex", 600), FileNotFoundError("nope"), PermissionError("denied"),
])
def test_generate_wraps_run_errors(cfg, exc):
    def fake_run(cmd, **kw):
        raise exc

    with pytest.raises(images.ImageGenerationError):
        images.generate("x", "square", "n", cfg, run=fake_run)


def test_unknown_shape(cfg):
    with pytest.raises(ValueError, match="shape"):
        images.generate("x", "round", "n", cfg, run=lambda *a, **k: None)


@pytest.mark.parametrize("content", [b"not an image at all", "truncated"])
def test_generate_rejects_a_broken_image_from_codex(cfg, content):
    def fake_run(cmd, **kw):
        target = cfg.codex_images_dir / THREAD / "exec-1.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        if content == "truncated":
            Image.new("RGB", (1536, 1024), (10, 120, 200)).save(target)
            target.write_bytes(target.read_bytes()[:2000])
        else:
            target.write_bytes(content)
        return SimpleNamespace(returncode=0, stdout=_started(), stderr="")

    with pytest.raises(images.ImageGenerationError, match="not a usable image"):
        images.generate("x", "square", "n", cfg, run=fake_run)
    assert not any(Path(cfg.images_dir).iterdir())  # nothing half-made is left behind


# ------------------------------------------------------------------ Codex missing, prompts and batch files


@pytest.mark.parametrize("codex", ["missing", None])
def test_generate_without_codex_says_how_to_fix_it(cfg, codex):
    cfg = dataclasses.replace(cfg, codex_path=None if codex is None else cfg.codex_path.with_name("no-codex"))
    with pytest.raises(images.ImageGenerationError, match="Codex isn't installed; install it or set codex_path"):
        images.generate("x", "square", "n", cfg, run=lambda *a, **k: pytest.fail("Codex must not run"))
    assert not Path(cfg.images_dir).exists()  # nothing was made


HOSTILE = ['" & calc & "', "%PATH% ^& whoami | more > out.txt", "$(Remove-Item x) `n `$env:USERPROFILE",
           "سلام \"كيف\" \u0645ی\u200cخواهم", "a\nb\r\nc", "!x! (y)"]


@pytest.mark.parametrize("prompt", HOSTILE)
@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_the_prompt_is_never_a_command_line_argument(cfg, monkeypatch, prompt, platform):
    monkeypatch.setattr(images.sys, "platform", platform)
    cfg = dataclasses.replace(cfg, codex_path=cfg.codex_path.with_name("codex.cmd"))
    cfg.codex_path.write_bytes(b"")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        _make_codex_image(cfg)
        return SimpleNamespace(returncode=0, stdout=_started(), stderr="")

    images.generate(prompt, "square", "n", cfg, run=fake_run)
    [(cmd, kwargs)] = calls
    assert cmd == images.codex_command(cfg) and cmd[-1] == "-"
    assert prompt in kwargs["input"] and not any(prompt in arg for arg in cmd)
    assert images.batch_problem(cmd, windows=True) is None  # every argument is plain enough for cmd.exe


@pytest.mark.parametrize("model", ["gpt&calc", "a b", "m%PATH%", "x|y", "x^y", "x<y", "x>y", "x!y", "(x)", ""])
@pytest.mark.parametrize("name", ["codex.cmd", "codex.CMD", "codex.bat", "Codex.Bat"])
def test_a_windows_batch_file_runs_only_with_plain_arguments(cfg, monkeypatch, model, name):
    monkeypatch.setattr(images.sys, "platform", "win32")
    cfg = dataclasses.replace(cfg, codex_path=cfg.codex_path.with_name(name), codex_model=model)
    cfg.codex_path.write_bytes(b"")
    with pytest.raises(images.ImageGenerationError, match="Windows batch file"):
        images.generate("x", "square", "n", cfg, run=lambda *a, **k: pytest.fail("Codex must not run"))
    assert not Path(cfg.images_dir).exists()


@pytest.mark.parametrize("name, windows, refused", [
    ("codex.cmd", True, True), ("codex.bat", True, True), ("codex.exe", True, False), ("codex", True, False),
    ("codex.cmd", False, False), ("codex", False, False),
])
def test_only_windows_batch_files_need_plain_arguments(cfg, name, windows, refused):
    cmd = images.codex_command(dataclasses.replace(cfg, codex_path=cfg.codex_path.with_name(name),
                                                   codex_model="bad model&x"))
    assert (images.batch_problem(cmd, windows=windows) is not None) is refused


@pytest.mark.parametrize("thread", ["C:evil", "..", ".", "a/b", "a\\b", "-x", "a b"])
def test_a_strange_thread_id_is_refused(cfg, thread):
    _make_codex_image(cfg, "evil-elsewhere")
    fake_run = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=_started(thread), stderr="")  # noqa: E731
    with pytest.raises(images.ImageGenerationError, match="thread"):
        images.generate("x", "square", "n", cfg, run=fake_run)
