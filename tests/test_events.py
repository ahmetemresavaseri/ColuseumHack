from __future__ import annotations

import events as wall_events


def test_call_started_shape():
    event = wall_events.call_started(
        "call-1",
        "glanz-ag",
        company_name="Glanz AG",
        caller="+41",
        locale="de-CH",
    )
    assert event["type"] == "CallStarted"
    assert event["callId"] == "call-1"
    assert event["companyId"] == "glanz-ag"
    assert event["companyName"] == "Glanz AG"
    assert event["caller"] == "+41"
    assert event["locale"] == "de-CH"
    assert "timestamp" in event


def test_slot_saved_stringifies_value():
    event = wall_events.slot_saved("c", "co", slot="rooms", value=4)
    assert event["type"] == "SlotSaved"
    assert event["slot"] == "rooms"
    assert event["value"] == "4"


def test_transcript_turn_carries_seq_and_speaker():
    event = wall_events.transcript_turn(
        "c", "co", seq=2, speaker="Caller", text="hi"
    )
    assert event["type"] == "TranscriptTurn"
    assert event["seq"] == 2
    assert event["speaker"] == "Caller"
    assert event["text"] == "hi"
    assert event["isFinal"] is True


def test_event_sink_collects():
    sink = wall_events.EventSink()
    sink(wall_events.agent_speaking_start("c", "co"))
    sink(wall_events.agent_speaking_end("c", "co"))
    assert [e["type"] for e in sink.events] == [
        "AgentSpeakingStart",
        "AgentSpeakingEnd",
    ]
