from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    qth_locator: str = os.getenv("QTH_LOCATOR", "JO00AA").upper()
    callsign: str = os.getenv("CALLSIGN", "N0CALL").upper()
    bands: tuple[str, ...] = tuple(
        b.strip().lower() for b in os.getenv("BANDS", "6m,10m,12m,15m,17m,20m,40m,60m,80m,4m,2m,70cm,23cm").split(",") if b.strip()
    )
    local_rx_radius_km: float = _float("LOCAL_RX_RADIUS_KM", 325.0)
    retention_hours: int = _int("RETENTION_HOURS", 72)
    analyse_interval_seconds: int = _int("ANALYSE_INTERVAL_SECONDS", 30)
    dashboard_timezone: str = os.getenv("DASHBOARD_TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin"
    activity_sample_seconds: int = _int("ACTIVITY_SAMPLE_SECONDS", 60)
    activity_retention_days: int = _int("ACTIVITY_RETENTION_DAYS", 14)
    dashboard_default_layer: str = os.getenv("DASHBOARD_DEFAULT_LAYER", "hf").strip().lower() or "hf"
    hf_layer_bands: tuple[str, ...] = tuple(
        b.strip().lower() for b in os.getenv("HF_LAYER_BANDS", "6m,10m,12m,15m,17m,20m,40m,60m,80m").split(",") if b.strip()
    )
    vhf_layer_bands: tuple[str, ...] = tuple(
        b.strip().lower() for b in os.getenv("VHF_LAYER_BANDS", "4m,2m,70cm,23cm").split(",") if b.strip()
    )
    dashboard_decision_cache_seconds: int = _int("DASHBOARD_DECISION_CACHE_SECONDS", 20)
    vhf_intel_cache_seconds: int = _int("VHF_INTEL_CACHE_SECONDS", 30)
    dashboard_secondary_cache_seconds: int = _int("DASHBOARD_SECONDARY_CACHE_SECONDS", 15)

    pskr_host: str = os.getenv("PSKR_HOST", "mqtt.pskreporter.info")
    pskr_port: int = _int("PSKR_PORT", 1884)
    pskr_tls: bool = _bool("PSKR_TLS", True)

    rbn_host: str = os.getenv("RBN_HOST", "telnet.reversebeacon.net")
    rbn_cw_port: int = _int("RBN_CW_PORT", 7000)
    rbn_ft8_port: int = _int("RBN_FT8_PORT", 7001)
    rbn_node_url: str = os.getenv("RBN_NODE_URL", "https://www.reversebeacon.net/nodes/detail_json.php")
    rbn_node_refresh_minutes: int = _int("RBN_NODE_REFRESH_MINUTES", 60)

    # Passive DX-Cluster feed for real SSB activity. The default is a public
    # DXSpider node; host/port remain configurable so the collector can be
    # switched without a software update.
    dxcluster_enabled: bool = _bool("DXCLUSTER_ENABLED", True)
    dxcluster_host: str = os.getenv("DXCLUSTER_HOST", "dxspider.co.uk").strip() or "dxspider.co.uk"
    dxcluster_port: int = _int("DXCLUSTER_PORT", 7300)
    dxcluster_login: str = os.getenv("DXCLUSTER_LOGIN", os.getenv("CALLSIGN", "N0CALL")).strip().upper()
    dxcluster_silence_seconds: int = _int("DXCLUSTER_SILENCE_SECONDS", 180)
    ssb_window_seconds: int = _int("SSB_WINDOW_SECONDS", 600)
    primary_prop_mode: str = os.getenv("PRIMARY_PROP_MODE", "ssb").strip().lower()

    adif_resource_url: str = os.getenv("ADIF_RESOURCE_URL", "https://www.adif.org.uk/317/ADIF_317_resources_2026_03_22.zip")
    adif_refresh_hours: int = _int("ADIF_REFRESH_HOURS", 168)

    # Callsign-prefix catalogue for enriching SSB/CW cluster/RBN spots.
    cty_resource_url: str = os.getenv("CTY_RESOURCE_URL", "https://www.country-files.com/cty/cty.dat")
    cty_refresh_hours: int = _int("CTY_REFRESH_HOURS", 72)

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    telegram_alerts: bool = _bool("TELEGRAM_ALERTS", True)
    telegram_commands: bool = _bool("TELEGRAM_COMMANDS", True)
    telegram_cooldown_minutes: int = _int("TELEGRAM_COOLDOWN_MINUTES", 20)

    rare_lookback_days: int = _int("RARE_LOOKBACK_DAYS", 90)
    rare_live_minutes: int = _int("RARE_LIVE_MINUTES", 15)
    rare_min_learning_days: int = _int("RARE_MIN_LEARNING_DAYS", 7)
    rare_min_stars: int = _int("RARE_MIN_STARS", 2)
    rare_live_min_rx: int = _int("RARE_LIVE_MIN_RX", 1)
    rare_watch_dxcc: tuple[int, ...] = tuple(
        int(x.strip()) for x in os.getenv("RARE_WATCH_DXCC", "").split(",") if x.strip().isdigit()
    )

    dx_live_minutes: int = _int("DX_LIVE_MINUTES", 15)
    dx_live_limit: int = _int("DX_LIVE_LIMIT", 18)
    dx_live_min_rx: int = _int("DX_LIVE_MIN_RX", 1)
    dx_live_min_score: int = _int("DX_LIVE_MIN_SCORE", 45)

    space_weather_enabled: bool = _bool("SPACE_WEATHER_ENABLED", True)
    space_weather_refresh_seconds: int = _int("SPACE_WEATHER_REFRESH_SECONDS", 300)
    noaa_sfi_url: str = os.getenv("NOAA_SFI_URL", "https://services.swpc.noaa.gov/products/summary/10cm-flux.json")
    noaa_kp_url: str = os.getenv("NOAA_KP_URL", "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    noaa_wind_url: str = os.getenv("NOAA_WIND_URL", "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json")
    noaa_mag_url: str = os.getenv("NOAA_MAG_URL", "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json")
    noaa_scales_url: str = os.getenv("NOAA_SCALES_URL", "https://services.swpc.noaa.gov/products/noaa-scales.json")
    noaa_xray_url: str = os.getenv("NOAA_XRAY_URL", "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json")
    noaa_indices_url: str = os.getenv("NOAA_INDICES_URL", "https://services.swpc.noaa.gov/text/current-space-weather-indices.txt")
    noaa_daily_solar_url: str = os.getenv("NOAA_DAILY_SOLAR_URL", "https://services.swpc.noaa.gov/text/daily-solar-indices.txt")

    watch_score: int = _int("WATCH_SCORE", 30)
    open_score: int = _int("OPEN_SCORE", 55)
    strong_score: int = _int("STRONG_SCORE", 75)

    db_path: str = os.getenv("DB_PATH", "/app/data/hamspotter.db")
    min_dx_km: dict[str, float] = field(default_factory=lambda: {
        "6m": _float("MIN_DX_6M_KM", 500),
        "10m": _float("MIN_DX_10M_KM", 800),
        "12m": _float("MIN_DX_12M_KM", 1000),
        "15m": _float("MIN_DX_15M_KM", 1200),
        "17m": _float("MIN_DX_17M_KM", 1500),
        "20m": _float("MIN_DX_20M_KM", 2500),
        "40m": _float("MIN_DX_40M_KM", 1800),
        "60m": _float("MIN_DX_60M_KM", 1200),
        "80m": _float("MIN_DX_80M_KM", 1200),
        "4m": _float("MIN_DX_4M_KM", 300),
        "2m": _float("MIN_DX_2M_KM", 200),
        "70cm": _float("MIN_DX_70CM_KM", 150),
        "23cm": _float("MIN_DX_23CM_KM", 100),
    })
    windows_seconds: dict[str, int] = field(default_factory=lambda: {
        "6m": 180,
        "10m": 300,
        "12m": 300,
        "15m": 480,
        "17m": 480,
        "20m": 600,
        "40m": 600,
        "60m": 900,
        "80m": 900,
        "4m": 300,
        "2m": 300,
        "70cm": 600,
        "23cm": 600,
    })


settings = Settings()
