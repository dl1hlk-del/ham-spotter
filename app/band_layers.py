from __future__ import annotations

from typing import Iterable

from .config import settings

LAYER_HF = "hf"
LAYER_VHF = "vhf"


def normalize_layer(value: str | None) -> str:
    raw = str(value or settings.dashboard_default_layer or LAYER_HF).strip().lower()
    if raw in {"vhf", "vhfuhf", "vhf-uhf", "high", "vhf_uhf_shf", "vhfuhfshf"}:
        return LAYER_VHF
    return LAYER_HF


def configured_layer_bands(layer: str | None) -> tuple[str, ...]:
    layer = normalize_layer(layer)
    wanted = settings.vhf_layer_bands if layer == LAYER_VHF else settings.hf_layer_bands
    configured = set(settings.bands)
    return tuple(b for b in wanted if b in configured)


def filter_rows_by_layer(rows: Iterable[dict], layer: str | None) -> list[dict]:
    allowed = set(configured_layer_bands(layer))
    return [row for row in rows if str(row.get("band") or "").lower() in allowed]


def layer_label(layer: str | None) -> str:
    return "VHF/UHF/SHF" if normalize_layer(layer) == LAYER_VHF else "HF + 6 m"
