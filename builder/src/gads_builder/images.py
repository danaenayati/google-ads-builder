"""Make an image with the Codex CLI (on macOS the one bundled in the ChatGPT app; on Windows codex.exe; elsewhere
`codex` on PATH; ChatGPT Plus login, no API key) and fit it to Google Ads' exact sizes. Changes nothing in
Google Ads."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

from .config import Config, windows_codex
from .plans import slugify

SIZES = {"landscape": (1200, 628), "square": (1200, 1200), "portrait": (1200, 1500)}
ASK = {"landscape": "wide landscape (3:2)", "square": "square (1:1)", "portrait": "tall portrait (2:3)"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
NOT_INSTALLED = "Codex isn't installed; install it or set codex_path in builder.toml"
# On Windows only codex.exe runs, by its full path: a .cmd or .bat file would run through cmd.exe, which
# reads its own command line by other rules. The prompt never goes on the command line (Codex reads it
# from stdin), so nothing needs a batch file.
WINDOWS_EXE_ONLY = ("On Windows, codex_path must be the full path of codex.exe (such as C:/Users/YOU/AppData/"
                    "Roaming/npm/.../bin/codex.exe), never a .cmd or .bat file; see docs/SETUP.md")
_THREAD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # a folder name: no / \ : or ".."


class ImageGenerationError(Exception):
    pass


def fit(src: Path, shape: str, dest: Path) -> Path:
    """Center-crop to the target ratio, then resize to the exact pixel size (JPEG, quality 90)."""
    tw, th = SIZES[shape]
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w * th > h * tw:
            cw, ch = h * tw // th, h
        else:
            cw, ch = w, w * th // tw
        left, top = (w - cw) // 2, (h - ch) // 2
        im = im.crop((left, top, left + cw, top + ch)).resize((tw, th), Image.Resampling.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "JPEG", quality=90, optimize=True)
    return dest


def _thread_id(stdout: str) -> str | None:
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    return None


def _tails(proc) -> str:
    err = (getattr(proc, "stderr", "") or "")[-500:]
    out = (getattr(proc, "stdout", "") or "")[-500:]
    return f"stderr: {err} | stdout: {out}"


def codex_program(cfg: Config, windows: bool | None = None) -> str:
    """The Codex program to run: as configured, and on Windows only a full path to a .exe, normalised as
    Windows reads it (config.windows_codex). Raises ImageGenerationError otherwise."""
    windows = sys.platform == "win32" if windows is None else windows
    if not windows:
        return str(cfg.codex_path)
    program = windows_codex(str(cfg.codex_path))
    if program is None:
        raise ImageGenerationError(f"{WINDOWS_EXE_ONLY} (codex_path: {cfg.codex_path})")
    return program


def codex_command(cfg: Config, program: str | None = None) -> list[str]:
    """The fixed Codex command. The final "-" makes Codex read the prompt from stdin, so no prompt or plan
    text is ever an argument."""
    return [program or str(cfg.codex_path), "exec", "--json", "--skip-git-repo-check", "-s", "read-only",
            "-m", cfg.codex_model, "-c", 'model_reasoning_effort="low"', "-"]


def generate(prompt: str, shape: str, name: str, cfg: Config, run=subprocess.run) -> dict:
    if shape not in SIZES:
        raise ValueError(f"shape must be one of {', '.join(SIZES)}")
    if cfg.codex_path is None or not Path(cfg.codex_path).is_file():
        raise ImageGenerationError(NOT_INSTALLED + (f" (not found: {cfg.codex_path})" if cfg.codex_path else ""))
    cmd = codex_command(cfg, codex_program(cfg))
    request = (
        f"Use your image generation tool to create ONE {ASK[shape]} image. {prompt}\n"
        "Keep the main subject centred with space around it; the image will be cropped to several shapes.\n"
        "No text, letters, logos or watermarks unless the request above explicitly asks for them.\n"
        "Generate it once, then reply with one short sentence."
    )
    slug = slugify(name, "image")
    # A new folder every time, so a regenerated image never replaces one a drafted plan points to.
    base = Path(cfg.images_dir) / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{slug}"
    folder, n = base, 2
    while True:
        try:
            folder.mkdir(parents=True)
            break
        except FileExistsError:
            folder, n = base.with_name(f"{base.name}-{n}"), n + 1

    def fail(message: str):
        try:
            folder.rmdir()  # only removes it while still empty
        except OSError:
            pass
        raise ImageGenerationError(message)

    try:
        proc = run(cmd, input=request, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=600, cwd=str(folder))
    except subprocess.TimeoutExpired:
        fail("Codex took longer than 10 minutes and was stopped. Is the VPN on?")
    except (FileNotFoundError, OSError) as e:
        fail(f"Could not start Codex ({cfg.codex_path}): {e}")
    if proc.returncode != 0:
        fail(f"Codex failed (exit {proc.returncode}). Is the VPN on? {_tails(proc)}")
    thread = _thread_id(proc.stdout)
    if not thread or not _THREAD.fullmatch(thread) or thread in (".", ".."):
        fail(f"Codex gave no thread ID, so its image can't be found. {_tails(proc)}")
    source_dir = Path(cfg.codex_images_dir) / thread
    found = sorted(
        (p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda p: p.stat().st_mtime,
    ) if source_dir.is_dir() else []
    if not found:
        fail(f"Codex made no image (looked in {source_dir}). Is the VPN on? {_tails(proc)}")
    source = found[-1]
    try:
        with Image.open(source) as im:
            im.verify()
        with Image.open(source) as im:
            im.load()  # verify() skips the pixel data; a truncated file only fails here
    except Exception as e:  # Pillow raises many types (OSError, SyntaxError, ...) for broken files
        fail(f"Codex made a file that is not a usable image ({source}): {type(e).__name__}: {e}")
    (folder / "prompt.txt").write_text(request + "\n", encoding="utf-8", newline="\n")
    original = folder / f"original{source.suffix.lower()}"
    shutil.copy2(source, original)
    w, h = SIZES[shape]
    final = fit(original, shape, folder / f"{slug}-{w}x{h}.jpg")
    return {"image": str(final), "original": str(original), "shape": shape}
