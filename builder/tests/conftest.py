import dataclasses
import socket
import threading
from pathlib import Path

import grpc
import grpc.aio
import pytest
from PIL import Image

from gads_builder import checks, confirm
from gads_builder.config import AppRef, Config


@pytest.fixture(scope="session")
def ads_client():
    """A real GoogleAdsClient that never touches the network.

    Credentials are passed as a ready object, so no OAuth token refresh happens.
    """
    from google.ads.googleads.client import GoogleAdsClient
    from google.oauth2.credentials import Credentials

    return GoogleAdsClient(
        credentials=Credentials(token="offline"),
        developer_token="offline",
        use_proto_plus=True,
    )


@pytest.fixture
def cfg(tmp_path):
    return Config(
        customer_id="1234567890",
        currency="USD",
        daily_budget_cap=500.0,
        apps=(AppRef("GOOGLE_APP_STORE", "com.example.app"),),
        country_code="XX",
        credentials=tmp_path / "google-ads.yaml",
        plans_dir=tmp_path / "plans",
        insights_dir=tmp_path / "insights",
        images_dir=tmp_path / "images",
        codex_path=tmp_path / "codex",
        codex_model="test-model",
        codex_images_dir=tmp_path / "codex-images",
    )


@pytest.fixture
def make_png(tmp_path):
    def _make(w: int, h: int, name: str | None = None, color=(200, 80, 40)) -> Path:
        path = tmp_path / (name or f"img-{w}x{h}.png")
        Image.new("RGB", (w, h), color).save(path)
        return path

    return _make


def _app_plan_dict(**overrides) -> dict:
    plan = {
        "kind": "app_campaign",
        "name": "Test City Launch",
        "subtype": "APP_CAMPAIGN",
        "app_store": "GOOGLE_APP_STORE",
        "app_id": "com.example.app",
        "goal": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
        "target_cpa": 12.5,
        "daily_budget": 300.0,
        "currency": "USD",
        "conversion_actions": ["customers/1234567890/conversionActions/111"],
        "locations": ["geoTargetConstants/1000001"],
        "languages": ["languageConstants/1000", "languageConstants/1019"],
        "headlines": [
            "Fast workouts",
            "Train in minutes",
            "Safe and simple",
            "Book your first class",
            "Lower prices today",
        ],
        "descriptions": [
            "Book a class in seconds and pay less on every visit.",
            "Friendly coaches across the city, day and night.",
            "Get fitter faster with live progress tracking.",
            "Download now and save on your first class.",
            "Trusted by thousands of members.",
        ],
        "images": [],
        "videos": [{"youtube_id": "abcdefghijk"}],
    }
    plan.update(overrides)
    return plan


@pytest.fixture
def app_plan():
    return _app_plan_dict


def _search_plan_dict(**overrides) -> dict:
    """A Search plan with no errors and no warnings under search_cfg. A new copy every call."""
    plan = {
        "kind": "search_campaign",
        "name": "Springfield Gyms - Search",
        "daily_budget": 80.0,
        "currency": "EUR",
        "bidding": "MAXIMIZE_CONVERSIONS",
        "locations": ["geoTargetConstants/1000001"],
        "languages": ["languageConstants/1000"],
        "negative_keywords": [{"text": "gym jobs", "match": "PHRASE"}],
        "ad_groups": [{
            "name": "Gym membership",
            "keywords": [
                {"text": "gym membership", "match": "PHRASE"},
                {"text": "gym membership springfield", "match": "EXACT"},
                {"text": "monthly gym pass", "match": "PHRASE"},
                {"text": "join a gym", "match": "PHRASE"},
                {"text": "cheap gym membership", "match": "EXACT"},
            ],
            "negative_keywords": [{"text": "free gym", "match": "EXACT"}],
            "ad": {
                "headlines": [
                    "Gym Membership in Springfield", "Flexible Gym Membership", "Join Pulse Fitness Today",
                    "No Joining Fee This Month", "Classes Every Day", "Train With Expert Coaches",
                    "Open Early and Late", "Book a Free Trial Class", "Cancel Any Time",
                    "Track Workouts in the App", "Yoga, Spin and Boxing", "Friendly Local Gyms",
                    "Start in Minutes", "Members Save on Classes", "Get Fit Your Way",
                ],
                "descriptions": [
                    "Join a friendly Springfield gym with classes every day and coaches who know your name.",
                    "Book classes, track workouts and pay monthly in the Pulse app. Cancel any time.",
                    "Yoga, spin, boxing and strength classes for every level, early morning to late.",
                    "Try your first class free and see why members keep coming back week after week.",
                ],
                "final_url": "https://www.example.com/gym",
                "path1": "gym",
                "path2": "join",
            },
        }],
        "sitelinks": [
            {"text": "Class Schedule", "url": "https://www.example.com/classes",
             "desc1": "See every class this week", "desc2": "Book a spot in seconds"},
            {"text": "Membership Prices", "url": "https://www.example.com/prices",
             "desc1": "Simple monthly plans", "desc2": "No joining fee this month"},
            {"text": "Free Trial Class", "url": "https://www.example.com/trial",
             "desc1": "Try any class once for free", "desc2": "No card needed"},
            {"text": "Find a Gym", "url": "https://www.example.com/locations",
             "desc1": "Gyms across Springfield", "desc2": "Open early and late"},
        ],
        "callouts": ["No Joining Fee", "Cancel Any Time", "Expert Coaches", "Classes Every Day"],
        "snippets": [{"header": "Types", "values": ["Yoga", "Spin", "Boxing", "Strength"]}],
        "brief": {"sources": {"Gym Membership in Springfield": "proven", "Classes Every Day": "proven"}},
    }
    plan.update(overrides)
    return plan


@pytest.fixture
def search_plan():
    return _search_plan_dict


@pytest.fixture
def search_cfg(cfg):
    """The account for Search tests: EUR, the example.com domain, and the brand word "Pulse"."""
    return dataclasses.replace(cfg, currency="EUR", domains=("example.com",), brand_words=("Pulse",))


def _demand_gen_plan_dict(files: dict, **overrides) -> dict:
    """A Demand Gen plan with no errors and no warnings under search_cfg. A new copy every call. `files`
    holds the paths of the test images (see the demand_gen_plan fixture)."""
    plan = {
        "kind": "demand_gen_campaign",
        "name": "Pulse Springfield - Demand Gen",
        "daily_budget": 30.0,
        "currency": "EUR",
        "bidding": "MAXIMIZE_CONVERSIONS",
        "conversion_goals": [{"category": "PURCHASE", "origin": "WEBSITE"}],
        "ad_groups": [{
            "name": "Springfield members",
            "locations": ["geoTargetConstants/1000001"],
            "languages": ["languageConstants/1000"],
            "channels": ["youtube_in_stream", "youtube_in_feed", "youtube_shorts", "discover", "gmail"],
            "ads": [
                {
                    "type": "image",
                    "headlines": ["Classes Every Day", "Join Pulse Fitness", "Train With Expert Coaches",
                                  "No Joining Fee This Month", "Open Early and Late"],
                    "descriptions": [
                        "Join a friendly Springfield gym with classes every day and coaches who know your name.",
                        "Book classes, track workouts and pay monthly in the Pulse app. Cancel any time.",
                        "Yoga, spin, boxing and strength classes for every level, early morning to late.",
                        "Try your first class free and see why members keep coming back week after week.",
                        "Gyms across Springfield, open early and late, with free parking at every club.",
                    ],
                    "business_name": "Pulse Fitness",
                    "final_url": "https://www.example.com/join",
                    "marketing_images": [{"file": files["landscape"]}],
                    "square_marketing_images": [{"file": files["square"]}],
                    "portrait_marketing_images": [{"file": files["portrait"]}],
                    "logo_images": [{"file": files["logo"]}],
                },
                {
                    "type": "video",
                    "videos": [{"youtube_id": "abcdefghijk"}],
                    "headlines": ["Classes Every Day", "Join Pulse Fitness", "Train With Expert Coaches",
                                  "No Joining Fee This Month", "Open Early and Late"],
                    "long_headlines": [
                        "Join a friendly Springfield gym with classes every day",
                        "Yoga, spin, boxing and strength classes for every level",
                        "Book classes and track workouts in the Pulse app",
                        "Try your first class free at any Pulse gym in Springfield",
                        "Gyms across Springfield, open early and late",
                    ],
                    "descriptions": [
                        "Coaches who know your name and classes that fit your day.",
                        "Pay monthly in the app and cancel any time.",
                        "Every level welcome, from first class to marathon training.",
                        "Free parking at every club across Springfield.",
                        "Book a spot in seconds and see your progress each week.",
                    ],
                    "business_name": "Pulse Fitness",
                    "final_url": "https://www.example.com/classes",
                    "logo_images": [{"file": files["logo"]}],
                },
            ],
        }],
        "brief": {"sources": {"Classes Every Day": "proven", "Join Pulse Fitness": "new"}},
    }
    plan.update(overrides)
    return plan


@pytest.fixture
def demand_gen_plan(make_png):
    """Builds a clean Demand Gen plan dict (see _demand_gen_plan_dict) around four new test images."""
    files = {
        "landscape": str(make_png(1200, 628, "dg-landscape.png")),
        "square": str(make_png(1200, 1200, "dg-square.png")),
        "portrait": str(make_png(960, 1200, "dg-portrait.png")),
        "logo": str(make_png(1200, 1200, "dg-logo.png", color=(20, 20, 20))),
    }
    return lambda **overrides: _demand_gen_plan_dict(files, **overrides)


def _pmax_plan_dict(files: dict, **overrides) -> dict:
    """A Performance Max plan with no errors and no warnings under search_cfg. A new copy every call. `files`
    holds the paths of the test images (see the pmax_plan fixture)."""
    plan = {
        "kind": "pmax_campaign",
        "name": "Pulse Springfield - Performance Max",
        "daily_budget": 50.0,
        "currency": "EUR",
        "bidding": "MAXIMIZE_CONVERSIONS",
        "conversion_goals": [{"category": "PURCHASE", "origin": "WEBSITE"}],
        "locations": ["geoTargetConstants/1000001"],
        "languages": ["languageConstants/1000"],
        "business_name": "Pulse Fitness",
        "logos": [{"file": files["logo"]}],
        "asset_groups": [{
            "name": "Springfield members",
            "final_url": "https://www.example.com/join",
            "path1": "gym",
            "path2": "join",
            "headlines": [
                "Classes Every Day", "Join Pulse Fitness", "Train With Expert Coaches", "No Joining Fee This Month",
                "Open Early and Late", "Book a Free Trial Class", "Cancel Any Time", "Track Workouts in the App",
                "Yoga, Spin and Boxing", "Friendly Local Gyms", "Start in Minutes", "Members Save on Classes",
                "Get Fit Your Way", "Free Parking at Every Club", "Gyms Across Springfield",
            ],
            "long_headlines": [
                "Join a friendly Springfield gym with classes every day",
                "Yoga, spin, boxing and strength classes for every level",
                "Book classes and track workouts in the Pulse app",
                "Try your first class free at any Pulse gym in Springfield",
                "Gyms across Springfield, open early and late",
            ],
            "descriptions": [
                "Join a friendly Springfield gym with classes every day and coaches who know your name.",
                "Book classes, track workouts and pay monthly in the Pulse app. Cancel any time.",
                "Yoga, spin, boxing and strength classes for every level, early morning to late.",
                "Try your first class free and see why members keep coming back week after week.",
                "Free parking at every club across Springfield.",
            ],
            "marketing_images": [{"file": files["landscape"]}],
            "square_marketing_images": [{"file": files["square"]}],
            "portrait_marketing_images": [{"file": files["portrait"]}],
            "videos": [{"youtube_id": "abcdefghijk"}],
            "search_themes": ["gym membership springfield", "yoga classes near me"],
        }],
        "brief": {"sources": {"Classes Every Day": "proven", "Join Pulse Fitness": "new"}},
    }
    plan.update(overrides)
    return plan


@pytest.fixture
def pmax_plan(make_png):
    """Builds a clean Performance Max plan dict (see _pmax_plan_dict) around four new test images."""
    files = {
        "landscape": str(make_png(1200, 628, "pm-landscape.png")),
        "square": str(make_png(1200, 1200, "pm-square.png")),
        "portrait": str(make_png(960, 1200, "pm-portrait.png")),
        "logo": str(make_png(1200, 1200, "pm-logo.png", color=(20, 20, 20))),
    }
    return lambda **overrides: _pmax_plan_dict(files, **overrides)


@pytest.fixture(autouse=True)
def _no_network_in_offline_tests(request, monkeypatch):
    """Offline tests must never reach Google (or anything else). Tests marked `live` are exempt."""
    if request.node.get_closest_marker("live"):
        return

    def refuse(*args, **kwargs):
        raise RuntimeError("network access in an offline test")

    # Windows has no native socketpair: Python builds one from a connection to its own loopback address,
    # and asyncio (FastMCP) needs it. Only a connect made inside socket.socketpair() is let through.
    real_connect, real_socketpair, pairing = socket.socket.connect, socket.socketpair, threading.local()

    def connect(self, *args, **kwargs):
        if getattr(pairing, "active", False):
            return real_connect(self, *args, **kwargs)
        refuse()

    def socketpair(*args, **kwargs):
        pairing.active = True
        try:
            return real_socketpair(*args, **kwargs)
        finally:
            pairing.active = False

    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", refuse)
    # A DNS lookup is network traffic too (and it would let a test depend on the network).
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "gethostbyname", refuse)
    monkeypatch.setattr(socket, "gethostbyname_ex", refuse)
    # gRPC's C core never touches Python's socket module, so the channels are blocked too.
    monkeypatch.setattr(grpc, "secure_channel", refuse)
    monkeypatch.setattr(grpc, "insecure_channel", refuse)
    monkeypatch.setattr(grpc.aio, "secure_channel", refuse)
    monkeypatch.setattr(grpc.aio, "insecure_channel", refuse)


@pytest.fixture(autouse=True)
def _fresh_check_memory():
    """Passed checks live in server memory; every test starts (and ends) with none."""
    checks.clear_passes()
    yield
    checks.clear_passes()


@pytest.fixture(autouse=True)
def _no_real_approval_window(monkeypatch):
    """No test may open a real dialog. A test that reaches the approval window replaces it; a test of
    the real ask_person replaces the subprocess runner it uses (test_confirm.py)."""

    def refuse(*args, **kwargs):
        raise RuntimeError("the approval window was not replaced in this test")

    monkeypatch.setattr(confirm, "ask_person", refuse)
    monkeypatch.setattr(confirm, "_run", refuse)
    # Server tests replace the window itself, on every system the tests run on (Linux too); whether a
    # system can show the window is tested on its own (test_confirm.py, test_server.py).
    monkeypatch.setattr(confirm, "window_supported", lambda: True)
