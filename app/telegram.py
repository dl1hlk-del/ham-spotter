from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .config import settings
from .db import get_band_state, mark_alerted, opening_history, opening_stats, set_health, status_snapshot
from .formatting import band_detail_text, compact_status_text, history_text, stats_text
from .rarity import live_snapshot, rarity_text
from .live_dx import live_dx_snapshot, telegram_text as live_dx_text
from .mode_live import live_mode_snapshot
from .space_weather import telegram_text as space_weather_text

log = logging.getLogger(__name__)


def _band_command_description(band: str) -> str:
    value = str(band).strip().lower()
    if value.endswith("cm") and value[:-2].isdigit():
        return f"Detailstatus {value[:-2]} Zentimeter"
    if value.endswith("m") and value[:-1].isdigit():
        return f"Detailstatus {value[:-1]} Meter"
    return f"Detailstatus {value.upper()}"


BOT_COMMANDS = (
    [{"command": "status", "description": "Übersicht aller überwachten Bänder"}]
    + [{"command": band, "description": _band_command_description(band)} for band in settings.bands]
    + [
        {"command": "history", "description": "Letzte gespeicherte Bandöffnungen"},
        {"command": "stats", "description": "7-Tage-Opening-Statistik"},
        {"command": "dx", "description": "Live DX im primären SSB-Modus"},
        {"command": "ssb", "description": "SSB-Ausbreitung aller Bänder"},
        {"command": "cw", "description": "CW-Ausbreitung aller Bänder"},
        {"command": "digital", "description": "FT8/FT4/Digital-Ausbreitung"},
        {"command": "rare", "description": "Persönlich seltene DXCC für dein QTH"},
        {"command": "funkwetter", "description": "Aktuelles Funkwetter (NOAA SWPC)"},
        {"command": "health", "description": "Status der Datenquellen"},
        {"command": "help", "description": "Befehle und kurze Hilfe anzeigen"},
        {"command": "test", "description": "Telegram-Verbindung testen"},
    ]
)


class Telegram:
    def __init__(self) -> None:
        self.enabled = bool(settings.telegram_bot_token and settings.telegram_chat_id)
        self.base = f"https://api.telegram.org/bot{settings.telegram_bot_token}" if settings.telegram_bot_token else ""
        self.offset = 0

    async def register_commands(self) -> bool:
        """Register the selectable Telegram command menu for the configured private chat."""
        if not self.enabled or not settings.telegram_commands:
            return False

        chat_id: int | str
        try:
            chat_id = int(settings.telegram_chat_id)
        except (TypeError, ValueError):
            chat_id = settings.telegram_chat_id

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    self.base + "/setMyCommands",
                    json={
                        "commands": BOT_COMMANDS,
                        "scope": {"type": "chat", "chat_id": chat_id},
                    },
                )
                r.raise_for_status()
                payload = r.json()
                if not payload.get("ok"):
                    raise RuntimeError(str(payload))

                r = await client.post(
                    self.base + "/setChatMenuButton",
                    json={
                        "chat_id": chat_id,
                        "menu_button": {"type": "commands"},
                    },
                )
                r.raise_for_status()
                payload = r.json()
                if not payload.get("ok"):
                    raise RuntimeError(str(payload))

            log.info("Telegram command menu registered: %d commands", len(BOT_COMMANDS))
            return True
        except Exception as exc:
            # Menu registration must never stop alerts or command polling.
            log.warning("Telegram command menu registration failed: %s", exc)
            return False

    async def send(self, text: str) -> bool:
        if not self.enabled or not settings.telegram_alerts:
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    self.base + "/sendMessage",
                    json={"chat_id": settings.telegram_chat_id, "text": text, "disable_web_page_preview": True},
                )
                r.raise_for_status()
                payload = r.json()
                if not payload.get("ok"):
                    raise RuntimeError(str(payload))
            set_health("telegram", "LIVE", seen=True)
            return True
        except Exception as exc:
            set_health("telegram", "ERROR", error=str(exc))
            log.warning("Telegram send failed: %s", exc)
            return False

    async def maybe_alert(self, band: str, state: str, score: int, sector: int | None, message: str) -> bool:
        row = get_band_state(band)
        if not row or not self.enabled:
            return False
        if state not in {"OPEN", "STRONG"}:
            return False
        now = int(time.time())
        last = int(row["alerted_at"] or 0)
        same_state = row["alerted_state"] == state
        old_sector = row["alerted_sector"]
        sector_changed = sector is not None and old_sector is not None and min((sector-old_sector)%360, (old_sector-sector)%360) >= 60
        cooldown_ok = (now - last) >= settings.telegram_cooldown_minutes * 60
        if same_state and not sector_changed:
            return False
        if same_state and sector_changed and not cooldown_ok:
            return False
        if not same_state and state != "STRONG" and last and not cooldown_ok:
            return False
        if await self.send(message):
            mark_alerted(band, state, sector, message, score)
            return True
        return False

    async def command_loop(self, stop_event: asyncio.Event) -> None:
        if not self.enabled or not settings.telegram_commands:
            set_health("telegram", "DISABLED" if not self.enabled else "LIVE")
            return
        await self.register_commands()
        set_health("telegram", "LIVE", seen=True)
        while not stop_event.is_set():
            try:
                async with httpx.AsyncClient(timeout=35) as client:
                    r = await client.get(self.base + "/getUpdates", params={"timeout": 25, "offset": self.offset, "allowed_updates": '["message"]'})
                    r.raise_for_status()
                    result = r.json().get("result", [])
                for update in result:
                    self.offset = max(self.offset, int(update["update_id"]) + 1)
                    msg = update.get("message") or {}
                    chat_id = str((msg.get("chat") or {}).get("id", ""))
                    if chat_id != str(settings.telegram_chat_id):
                        continue
                    text = str(msg.get("text", "")).strip().lower()
                    command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
                    if command in {"/status", "/open"}:
                        await self.send(self._status_text())
                    elif command in {f"/{b}" for b in settings.bands}:
                        await self.send(self._band_text(command[1:]))
                    elif command == "/dx":
                        await self.send(self._live_mode_text("ssb"))
                    elif command in {"/ssb", "/cw", "/digital"}:
                        await self.send(self._mode_status_text(command[1:]))
                    elif command == "/rare":
                        await self.send(rarity_text(live_snapshot(limit=10)))
                    elif command == "/funkwetter":
                        await self.send(space_weather_text())
                    elif command == "/health":
                        snap = status_snapshot()
                        lines = ["🩺 HAM SPOTTER HEALTH"]
                        for s in snap["sources"]:
                            lines.append(f"{s['source']}: {s['status']}")
                        await self.send("\n".join(lines))
                    elif command == "/history":
                        await self.send(history_text(opening_history(limit=6), qth=settings.qth_locator))
                    elif command == "/stats":
                        await self.send(stats_text(opening_stats(days=7), qth=settings.qth_locator))
                    elif command in {"/help", "/start"}:
                        await self.send(self._help_text())
                    elif command == "/test":
                        await self.send(f"✅ HAM Spotter Telegram-Test\nQTH {settings.qth_locator}\nCall {settings.callsign}\nBänder: {', '.join(settings.bands)}")
                set_health("telegram", "LIVE", seen=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                set_health("telegram", "ERROR", error=str(exc))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass

    def _help_text(self) -> str:
        lines = [
            f"📡 HAM SPOTTER – {settings.qth_locator} · {settings.callsign}",
            "",
            "Befehle:",
        ]
        for item in BOT_COMMANDS:
            lines.append(f"/{item['command']} – {item['description']}")
        lines.append("")
        lines.append("Tipp: Über die Menü-Schaltfläche in Telegram kannst du die Befehle direkt auswählen.")
        return "\n".join(lines)

    def _status_text(self) -> str:
        return compact_status_text(
            status_snapshot(),
            qth=settings.qth_locator,
            callsign=settings.callsign,
        )

    def _mode_status_text(self, mode: str) -> str:
        mode = str(mode or "ssb").lower()
        snap = status_snapshot()
        icon = {"ssb": "🎙️", "cw": "📻", "digital": "💻"}.get(mode, "📡")
        lines = [f"{icon} {mode.upper()} PROPAGATION – {settings.qth_locator}"]
        for row in snap.get("bands", []):
            base = row.get("details") or {}
            md = ((base.get("mode_scores") or {}).get(mode) or {})
            score = int(md.get("score") or 0)
            state = str(md.get("state") or "CLOSED")
            direction = str(md.get("direction_label") or "—")
            extra = ""
            if mode == "ssb":
                extra = f" · {int(md.get('unique_tx') or 0)} DX / {int(md.get('unique_rx') or 0)} Spotter"
            elif mode == "cw":
                extra = f" · {int(md.get('unique_tx') or 0)} Calls / {int(md.get('unique_rx') or 0)} Skimmer"
            else:
                extra = f" · {int(md.get('psk_unique_tx') or 0)} PSK-DX"
            lines.append(f"{str(row.get('band') or '').upper():>3}  {state} {score}/100 · {direction}{extra}")
        return "\n".join(lines)

    def _live_mode_text(self, mode: str) -> str:
        snap = live_mode_snapshot(mode, limit=10, minutes=settings.dx_live_minutes)
        stations = snap.get("stations") or []
        lines = [f"🎙️ LIVE SSB DX – {settings.qth_locator}", f"letzte {int(snap.get('live_minutes') or settings.dx_live_minutes)} Min."]
        if not stations:
            lines.append("Aktuell keine regional bestätigten SSB-DX-Spots.")
            return "\n".join(lines)
        for item in stations[:10]:
            freq = item.get("frequency_khz")
            freq_text = f" · {float(freq)/1000:.3f} MHz" if freq else ""
            dist = item.get("distance_km")
            dist_text = f" · {int(dist):,} km".replace(",", ".") if dist else ""
            lines.append(f"{item.get('call')} · {str(item.get('band') or '').upper()}{freq_text} · 👥 {int(item.get('regional_spotters') or item.get('local_rx') or 0)} Spotter{dist_text}")
        return "\n".join(lines)

    def _band_text(self, only_band: str) -> str:
        snap = status_snapshot()
        row = next((b for b in snap["bands"] if b["band"] == only_band), None)
        if not row:
            return f"⚪ Keine Daten für {only_band}"
        return band_detail_text(
            row["band"],
            row["state"],
            int(row["score"] or 0),
            row.get("direction_label") or "unbekannt",
            row.get("details") or {},
            qth=settings.qth_locator,
            callsign=settings.callsign,
        )
