# HAM Spotter V1.13.12 – Telegram Reopening Alert Fix

## Fixed

Telegram opening alerts now distinguish a genuinely new band opening from the persisted alert latch of an older opening.

Previously, a band that had once alerted as `OPEN` or `STRONG` could remain suppressed days later when it reached the same state again, because `alerted_state` stayed persisted in `band_state`.

V1.13.12 uses the persisted opening history to determine whether the previous alert belongs to the current continuous opening. A real `WATCH`/`CLOSED` gap starts a new opening and allows a new Telegram alert after the configured cooldown.

## Preserved behaviour

- same-state alerts inside one continuous opening remain de-duplicated
- reliable >=60° direction segments remain part of the same continuous opening for alert de-duplication
- `TELEGRAM_COOLDOWN_MINUTES` remains effective
- `OPEN` -> `STRONG` escalation behaviour is unchanged
- no scoring, thresholds, `.env`, database schema or data-source logic changes

## Scope

The fix is band-independent and applies to every configured band, including HF, 6 m, 4 m, 2 m, 70 cm and 23 cm.

Regression coverage explicitly includes 40 m, 6 m, 2 m and 70 cm.
