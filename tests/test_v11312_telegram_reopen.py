import asyncio

import pytest

import app.telegram as telegram_module
from app.config import settings
from app.telegram import Telegram, _alert_latch_belongs_to_current_opening


def _event(start_ts: int, *, end_ts: int | None, active: bool) -> dict:
    return {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "active": active,
    }


def test_alert_latch_tracks_sector_segments_as_one_opening(monkeypatch):
    now = 200_000
    events = [
        _event(now - 600, end_ts=None, active=True),
        _event(now - 1_800, end_ts=now - 600, active=False),
        _event(now - 7_200, end_ts=now - 1_800, active=False),
    ]
    monkeypatch.setattr(telegram_module, "opening_history", lambda limit, band: events)

    # The alert happened after the root start of the same continuous opening.
    assert _alert_latch_belongs_to_current_opening("6m", now - 3_600)


def test_alert_latch_is_stale_after_real_closed_gap(monkeypatch):
    now = 200_000
    events = [
        _event(now - 600, end_ts=None, active=True),
        _event(now - 7_200, end_ts=now - 1_800, active=False),
    ]
    monkeypatch.setattr(telegram_module, "opening_history", lambda limit, band: events)

    # A real inactive gap exists between the previous event and the current one.
    assert not _alert_latch_belongs_to_current_opening("40m", now - 3_600)


@pytest.mark.parametrize("band", ["40m", "6m", "2m", "70cm"])
def test_reopened_band_can_alert_again_for_hf_vhf_uhf(monkeypatch, band):
    now = 200_000
    last_alert = now - 3_600
    events = [
        _event(now - 600, end_ts=None, active=True),
        _event(now - 7_200, end_ts=now - 1_800, active=False),
    ]
    row = {
        "alerted_at": last_alert,
        "alerted_state": "OPEN",
        "alerted_sector": None,
    }

    monkeypatch.setattr(telegram_module.time, "time", lambda: now)
    monkeypatch.setattr(telegram_module, "get_band_state", lambda requested_band: row)
    monkeypatch.setattr(telegram_module, "opening_history", lambda limit, band: events)
    monkeypatch.setattr(settings, "telegram_cooldown_minutes", 20)

    marked = []
    monkeypatch.setattr(telegram_module, "mark_alerted", lambda *args: marked.append(args))

    bot = Telegram()
    bot.enabled = True

    async def fake_send(text: str) -> bool:
        return True

    monkeypatch.setattr(bot, "send", fake_send)

    assert asyncio.run(bot.maybe_alert(band, "OPEN", 94, None, "reopened"))
    assert marked and marked[0][0] == band


def test_same_opening_same_state_stays_deduplicated(monkeypatch):
    now = 200_000
    row = {
        "alerted_at": now - 3_600,
        "alerted_state": "OPEN",
        "alerted_sector": None,
    }
    events = [_event(now - 7_200, end_ts=None, active=True)]

    monkeypatch.setattr(telegram_module.time, "time", lambda: now)
    monkeypatch.setattr(telegram_module, "get_band_state", lambda band: row)
    monkeypatch.setattr(telegram_module, "opening_history", lambda limit, band: events)
    monkeypatch.setattr(settings, "telegram_cooldown_minutes", 20)

    bot = Telegram()
    bot.enabled = True

    async def fail_send(text: str) -> bool:
        raise AssertionError("same opening must not send another unchanged OPEN alert")

    monkeypatch.setattr(bot, "send", fail_send)

    assert not asyncio.run(bot.maybe_alert("40m", "OPEN", 98, None, "same opening"))


def test_reopen_still_respects_cooldown(monkeypatch):
    now = 200_000
    row = {
        "alerted_at": now - 600,
        "alerted_state": "OPEN",
        "alerted_sector": None,
    }
    events = [
        _event(now - 300, end_ts=None, active=True),
        _event(now - 3_600, end_ts=now - 900, active=False),
    ]

    monkeypatch.setattr(telegram_module.time, "time", lambda: now)
    monkeypatch.setattr(telegram_module, "get_band_state", lambda band: row)
    monkeypatch.setattr(telegram_module, "opening_history", lambda limit, band: events)
    monkeypatch.setattr(settings, "telegram_cooldown_minutes", 20)

    bot = Telegram()
    bot.enabled = True

    async def fail_send(text: str) -> bool:
        raise AssertionError("reopen inside cooldown must remain suppressed")

    monkeypatch.setattr(bot, "send", fail_send)

    assert not asyncio.run(bot.maybe_alert("2m", "OPEN", 90, None, "too soon"))
