import json

from app.rbn_nodes import parse_node_html, parse_node_json, parse_node_payload


def test_rbn_current_json_rows_are_parsed():
    payload = [
        {"id": "16345", "call": "3B8GL", "grid": "LG89RR", "cont": "AF", "lst_age": "online"},
        {"id": "16251", "call": "5Z4GO", "grid": "KI96UA", "cont": "AF", "lst_age": "online"},
    ]
    assert parse_node_json(payload) == [("3B8GL", "LG89RR"), ("5Z4GO", "KI96UA")]


def test_rbn_json_wrapper_and_aliases_are_supported():
    payload = {
        "nodes": [
            {"callsign": "DL1ABC-2", "locator": "JO61FR"},
            {"spotter": "W3LPL", "maidenhead": "FM19LG"},
            {"callsign": "INVALID", "locator": "NOPE"},
        ]
    }
    assert parse_node_json(payload) == [("DL1ABC", "JO61FR"), ("W3LPL", "FM19LG")]


def test_rbn_payload_prefers_json_content_type():
    text = json.dumps([{"call": "AA0O", "grid": "EL87PS"}])
    assert parse_node_payload(text, "application/json; charset=UTF-8") == [("AA0O", "EL87PS")]


def test_rbn_html_fallback_still_works():
    html = """
    <table>
      <tr><th>callsign</th><th>grid</th></tr>
      <tr><td>DL1ABC</td><td>JO61FR</td></tr>
    </table>
    """
    expected = [("DL1ABC", "JO61FR")]
    assert parse_node_html(html) == expected
    assert parse_node_payload(html, "text/html") == expected
