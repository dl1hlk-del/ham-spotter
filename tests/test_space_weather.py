from app.space_weather import parse_daily_solar, parse_running_a, parse_scales, xray_class


def test_xray_class():
    assert xray_class(1.4e-6) == "C1.4"
    assert xray_class(2.1e-5) == "M2.1"


def test_running_a():
    assert parse_running_a("#Running A 03-06-09-12\n7 3 0 1 2") == 7


def test_daily_solar():
    flux, ssn = parse_daily_solar("2026 03 13 120 105 440\n2026 04 09 98 79 123")
    assert flux == 98
    assert ssn == 79


def test_scales():
    assert parse_scales({"0":{"R":{"Scale":"1"},"S":{"Scale":"0"},"G":{"Scale":"2"}}}) == (1, 0, 2)
