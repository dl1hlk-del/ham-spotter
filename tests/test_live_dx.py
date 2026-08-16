from app.live_dx import _highlight_label, _highlight_score


def test_highlight_label_boundaries():
    assert _highlight_label(45) == "✨ interessant"
    assert _highlight_label(60) == "⭐ sehr interessant"
    assert _highlight_label(75) == "🔥 Top DX"


def test_six_meter_long_dx_scores_above_threshold():
    score = _highlight_score(
        band="6m",
        distance_km=3000,
        local_rx=3,
        best_snr=-8,
        rbn_rx=1,
        region="Europa",
        rarity_stars=0,
    )
    assert score >= 60


def test_marginal_local_lowband_is_not_highlighted():
    score = _highlight_score(
        band="80m",
        distance_km=1300,
        local_rx=1,
        best_snr=-15,
        rbn_rx=0,
        region="Europa",
        rarity_stars=0,
    )
    assert score < 45
