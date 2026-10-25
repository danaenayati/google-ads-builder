"""The example files in config/ that the setup guide tells people to copy."""

import asyncio
import json
from pathlib import Path

import pytest

from gads_builder import server
from gads_builder.config import load_config

CONFIG = Path(__file__).resolve().parents[2] / "config"


def _json(name: str) -> dict:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def _change_tools() -> set[str]:
    """The tools the server itself marks as changing the account."""
    tools = asyncio.run(server.mcp.list_tools())
    return {t.name for t in tools if t.annotations and t.annotations.destructive_hint}


def test_the_settings_example_asks_before_exactly_the_change_tools():
    settings = _json("claude-settings.json.example")
    assert set(settings) == {"permissions"} and set(settings["permissions"]) == {"ask", "deny"}
    ask = settings["permissions"]["ask"]
    assert len(ask) == len(set(ask))
    assert set(ask) == {f"mcp__google-ads-builder__{name}" for name in _change_tools()}
    assert _change_tools() == {"create_campaign_from_plan", "upload_images", "update_campaign_assets"}
    assert settings["permissions"]["deny"] == ["Read(~/.config/google-ads-mcp/**)"]


@pytest.mark.parametrize("system", ["mac", "windows"])
def test_the_mcp_example_has_only_the_official_server_and_the_builder(system):
    servers = _json("mcp.json.example")[system]["mcpServers"]
    assert set(servers) == {"google-ads", "google-ads-builder"}
    official, builder = servers["google-ads"], servers["google-ads-builder"]
    assert official["command"] == "uvx" and builder["command"] == "uv"
    assert official["args"] == ["--from", "git+https://github.com/googleads/google-ads-mcp.git", "google-ads-mcp"]
    assert builder["args"][:3] == ["run", "--frozen", "--project"] and builder["args"][-1] == "gads-builder"
    assert official["env"]["GOOGLE_APPLICATION_CREDENTIALS"].endswith(("/adc.json", "\\adc.json"))
    assert builder["env"]["GADS_BUILDER_CONFIG"].endswith(("/builder.toml", "\\builder.toml"))
    if system == "windows":
        assert official["env"]["PYTHONUTF8"] == builder["env"]["PYTHONUTF8"] == "1"
        assert builder["args"][3] == "C:\\Users\\YOU\\google-ads-builder\\builder"
    else:
        assert builder["args"][3] == "/Users/YOU/google-ads-builder/builder"


def test_the_builder_config_example_loads():
    cfg = load_config(CONFIG / "builder.toml.example")
    assert cfg.customer_id == "1234567890" and cfg.apps and cfg.domains == ("example.com",)
