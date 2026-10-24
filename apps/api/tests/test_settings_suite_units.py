"""Unit tests for the pure helpers behind the settings suite (no database)."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import conversations, icp, outbound, territories, waterfall
from app.services.prospecting import is_role_address
from app.services.workspace_settings import SendingProtection


def test_transcript_formats():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Ana>Hello there\n\n00:00:04.500 --> 00:00:06.000\n<v Ben>Hi Ana\n"
    parsed = conversations.parse_transcript(vtt, "call.vtt")
    assert [u["speaker"] for u in parsed] == ["Ana", "Ben"] and parsed[0]["start"] == 1.0

    srt = "1\n00:00:01,000 --> 00:00:02,000\nAna: One\n\n2\n00:00:02,500 --> 00:00:03,000\nBen: Two\n"
    assert [u["text"] for u in conversations.parse_transcript(srt, "call.srt")] == ["One", "Two"]

    plain = "Ana: First line\nBen: Second line\ncontinued here\n"
    parsed = conversations.parse_transcript(plain, "notes.txt")
    assert parsed[1]["text"].endswith("continued here")


def test_redaction_and_talk_time():
    utterances = [
        {"speaker": "Ana", "text": "Card 4111 1111 1111 1111 please", "start": 0.0, "end": 10.0},
        {"speaker": "Ben", "text": "Sure?", "start": 10.0, "end": 12.0},
    ]
    assert conversations.redact(utterances) == 1
    assert "4111" not in utterances[0]["text"] and utterances[0]["text"].endswith(" please")
    talk = conversations.talk_time(utterances)
    assert talk["unit"] == "seconds" and talk["speakers"][0]["speaker"] == "Ana" and talk["questions"] == 1


def test_territory_rules():
    account = SimpleNamespace(billing_state="TX", billing_country="USA", employee_count=750, industry="Healthcare", custom_fields={})
    texas = {"name": "Texas", "match": "all", "rules": [{"field": "state", "operator": "in", "values": ["tx", "OK"]}, {"field": "employee_count", "operator": "between", "values": ["500", "5000"]}]}
    assert territories.territory_matches(account, "account", texas)
    small = {"name": "SMB", "match": "all", "rules": [{"field": "employee_count", "operator": "lte", "values": ["100"]}]}
    assert not territories.territory_matches(account, "account", small)
    either = {"name": "Health", "match": "any", "priority": 5, "rules": [{"field": "industry", "operator": "contains", "values": ["health"]}, {"field": "state", "operator": "in", "values": ["CA"]}]}
    assert territories.territory_matches(account, "account", either)
    assert not territories.territory_matches(account, "lead", {**texas, "applies_to": ["account"]})
    assert territories.find_territory(account, "account", [texas, either])["name"] == "Health"  # lower priority number wins


def test_sending_window():
    protection = SendingProtection(send_days=[0, 1, 2, 3, 4], window_start_hour=8, window_end_hour=18, timezone="America/Chicago")
    saturday = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)
    assert not outbound.in_window(protection, saturday)
    monday_open = outbound.next_window(protection, saturday)
    assert monday_open.weekday() == 0 and outbound.in_window(protection, monday_open)
    tuesday_noon = datetime(2026, 10, 27, 17, 0, tzinfo=timezone.utc)  # 12:00 in Chicago
    assert outbound.in_window(protection, tuesday_noon) and outbound.next_window(protection, tuesday_noon) == tuesday_noon


def test_click_signature_and_content_checks():
    url = outbound.click_url("abc123", "https://aiva.example/pricing")
    assert "abc123" in url and outbound.click_signature("abc123", "https://aiva.example/pricing") in url
    assert outbound.click_signature("abc123", "https://evil.example") != outbound.click_signature("abc123", "https://aiva.example/pricing")
    issues = outbound.content_issues("FREE!!! Act now", "Click here " * 5)
    assert issues


def test_personas_and_role_addresses():
    persona = SimpleNamespace(title_keywords=["operations", "COO"], exclude_keywords=["assistant"], seniorities=[], departments=[])
    assert icp.persona_matches("VP of Operations", persona)
    assert icp.persona_matches("COO", persona)
    assert not icp.persona_matches("Operations Assistant", persona)
    assert not icp.persona_matches("Cooper Engineer", persona)
    assert is_role_address("info@acme.example") and is_role_address("Sales@acme.example")
    assert not is_role_address("jane.doe@acme.example")


def test_homepage_parsing():
    html = """<html><head><title>Acme HVAC | Heating & Cooling in Dallas</title>
    <meta name="description" content="Family-owned HVAC company serving Dallas since 1998.">
    <script type="application/ld+json">{"@type": "Organization", "telephone": "+1 214 555 0100", "sameAs": ["https://www.linkedin.com/company/acme-hvac"]}</script>
    </head><body><a href="tel:+12145550100">Call</a></body></html>"""
    values = waterfall.parse_homepage(html)
    assert values["company"].startswith("Acme HVAC")
    assert "linkedin.com/company/acme-hvac" in (values.get("linkedin") or "")
    assert values.get("phone")


def test_removal_matches_exact_addresses_only():
    from app.api.routes.data_mgmt import _addresses

    message = SimpleNamespace(to_recipients=[{"email": "JimBob@x.com"}], cc_recipients=[{"email": "bob@x.com.au"}], bcc_recipients=None)
    assert "bob@x.com" not in _addresses(message)
    message.cc_recipients.append({"email": "Bob@X.com"})
    assert "bob@x.com" in _addresses(message)


def test_recording_download_name_handles_any_title():
    from app.api.routes.conversations import _download_name

    header = _download_name(SimpleNamespace(title="Q3 review – Acme \"big\" / deal", recording_type="audio/mpeg"))
    header.encode("latin-1")  # must not raise
    assert header.startswith('attachment; filename="Q3 review Acme big deal.mp3"')
    assert "filename*=UTF-8''Q3%20review%20%E2%80%93" in header
