"""Start google-ads-builder exactly like Claude Code does and check its tools. No Google calls:
the config is loaded first (GADS_BUILDER_CONFIG, or the default ~/.config/google-ads-mcp/builder.toml),
then draft_plan is given an invalid kind, which is rejected before anything reaches Google. The
instructions must be short enough for Claude Code (it cuts them at about 4,000 characters), and the
playbook tool must answer. Works on macOS, Windows and Linux.

Usage (in the builder folder):  uv run --frozen python scripts/smoke.py"""

import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TIMEOUT = 60  # seconds to wait for each response

project = Path(__file__).resolve().parents[1]
stderr_file = tempfile.TemporaryFile(mode="w+b")
proc = subprocess.Popen(
    ["uv", "run", "--frozen", "--project", str(project), "gads-builder"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_file,
)
lines: queue.Queue = queue.Queue()


def pump():
    """Reads the server's stdout on its own thread (Windows can't wait on a pipe with select)."""
    for line in proc.stdout:
        lines.put(line)
    lines.put(None)


threading.Thread(target=pump, daemon=True).start()


def stop():
    try:
        proc.stdin.close()  # the server exits when its input ends
        proc.wait(timeout=15)
    except Exception:
        proc.kill()


def fail(reason):
    proc.kill()
    stderr_file.seek(0)
    print("SMOKE FAILED:", reason)
    print("server stderr (tail):")
    print(stderr_file.read()[-2000:].decode("utf-8", "replace"))
    sys.exit(1)


def send(msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
    proc.stdin.flush()


def recv(want):
    """Reads lines from the server's stdout until the response with id `want`, or fails after TIMEOUT."""
    deadline = time.monotonic() + TIMEOUT
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            fail(f"no response to request {want} within {TIMEOUT} seconds")
        try:
            line = lines.get(timeout=left)
        except queue.Empty:
            continue
        if line is None:
            fail(f"the server exited before answering request {want}")
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == want:
            return msg


send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}})
init = recv(1)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
tools = sorted(t["name"] for t in recv(2)["result"]["tools"])
send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
      "params": {"name": "draft_plan", "arguments": {"plan": {"kind": "nope"}}}})
reply = json.dumps(recv(3))
send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
      "params": {"name": "playbook", "arguments": {"section": "Safety"}}})
safety = json.dumps(recv(4))
stop()
instructions = init["result"].get("instructions") or ""
print("tools:", ", ".join(tools))
print("instructions:", len(instructions), "characters")
if "Builder config not found" in reply:
    fail("no builder.toml: save it in ~/.config/google-ads-mcp/ (or set GADS_BUILDER_CONFIG) and run again")
ok = (len(tools) == 15 and "unknown plan kind" in reply and 0 < len(instructions) < 2500
      and "`playbook` tool" in instructions and "## Safety" in safety and "Never retry" in safety)
if not ok:
    fail("expected 15 tools, the 'unknown plan kind' rejection, short instructions and the playbook's Safety section")
print("SMOKE OK")
