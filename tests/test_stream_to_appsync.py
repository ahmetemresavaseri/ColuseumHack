from __future__ import annotations

import importlib.util
from pathlib import Path

# Both Lambdas have a top-level `handler.py`; importing by file path keeps the
# stream-to-appsync one accessible even though conftest already added the
# input_agent dir to sys.path.
_STREAM_PATH = (
    Path(__file__).resolve().parent.parent
    / "lambdas"
    / "stream_to_appsync"
    / "handler.py"
)
_spec = importlib.util.spec_from_file_location("stream_to_appsync_handler", _STREAM_PATH)
assert _spec is not None and _spec.loader is not None
stream_handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stream_handler)
normalize_record = stream_handler.normalize_record


def _calls_meta_insert(call_id: str) -> dict:
    return {
        "eventName": "INSERT",
        "dynamodb": {
            "NewImage": {
                "callId": {"S": call_id},
                "sk": {"S": "meta"},
                "companyId": {"S": "glanz-ag"},
                "companyName": {"S": "Glanz AG"},
                "caller": {"S": "+1555"},
                "locale": {"S": "en-US"},
                "status": {"S": "Live"},
                "startedAt": {"S": "2026-05-22T10:00:00Z"},
                "updatedAt": {"S": "2026-05-22T10:00:00Z"},
            }
        },
    }


def _calls_turn_insert(call_id: str, seq: int, speaker: str, text: str) -> dict:
    return {
        "eventName": "INSERT",
        "dynamodb": {
            "NewImage": {
                "callId": {"S": call_id},
                "sk": {"S": f"turn#{seq:06d}"},
                "speaker": {"S": speaker},
                "transcriptChunk": {"S": text},
                "citations": {"L": []},
                "createdAt": {"S": "2026-05-22T10:00:01Z"},
            }
        },
    }


def _booking_modify(
    booking_id: str,
    old_slots: dict,
    new_slots: dict,
    new_brain: dict | None = None,
) -> dict:
    def _slots_to_ddb(slots: dict) -> dict:
        return {k: {"S": str(v)} for k, v in slots.items()}

    new_image = {
        "bookingId": {"S": booking_id},
        "sk": {"S": "current"},
        "callId": {"S": "call-xxx"},
        "companyId": {"S": "glanz-ag"},
        "slots": {"M": _slots_to_ddb(new_slots)},
        "updatedAt": {"S": "2026-05-22T10:00:02Z"},
    }
    old_image = {
        "bookingId": {"S": booking_id},
        "sk": {"S": "current"},
        "callId": {"S": "call-xxx"},
        "companyId": {"S": "glanz-ag"},
        "slots": {"M": _slots_to_ddb(old_slots)},
    }
    if new_brain:
        new_image["brain"] = {
            "M": {
                "serviceType": {"S": new_brain["serviceType"]},
                "price": {"N": str(new_brain["price"])},
                "currency": {"S": new_brain["currency"]},
                "needsPhotos": {"BOOL": new_brain.get("needsPhotos", False)},
            }
        }
    return {
        "eventName": "MODIFY",
        "dynamodb": {"NewImage": new_image, "OldImage": old_image},
    }


def test_meta_insert_yields_call_started():
    events = normalize_record(_calls_meta_insert("call-1"))
    assert len(events) == 1
    assert events[0]["type"] == "CallStarted"
    assert events[0]["callId"] == "call-1"
    assert events[0]["companyId"] == "glanz-ag"
    assert events[0]["payload"]["companyName"] == "Glanz AG"


def test_turn_insert_yields_transcript_turn():
    events = normalize_record(_calls_turn_insert("call-1", 2, "Caller", "hi"))
    assert len(events) == 1
    assert events[0]["type"] == "TranscriptTurn"
    assert events[0]["payload"]["seq"] == 2
    assert events[0]["payload"]["speaker"] == "Caller"
    assert events[0]["payload"]["text"] == "hi"


def test_meta_modify_to_ended_yields_call_ended():
    record = _calls_meta_insert("call-1")
    record["eventName"] = "MODIFY"
    record["dynamodb"]["OldImage"] = dict(record["dynamodb"]["NewImage"])
    record["dynamodb"]["NewImage"]["status"] = {"S": "Ended"}
    record["dynamodb"]["NewImage"]["endedReason"] = {"S": "completed"}
    events = normalize_record(record)
    assert any(e["type"] == "CallEnded" for e in events)


def test_booking_slot_diff_yields_slot_saved():
    record = _booking_modify(
        "booking-1",
        old_slots={"what": "MOVE_OUT_CLEANING"},
        new_slots={"what": "MOVE_OUT_CLEANING", "when": "tomorrow"},
    )
    events = normalize_record(record)
    types = [e["type"] for e in events]
    assert types == ["SlotSaved"]
    assert events[0]["payload"]["slot"] == "when"
    assert events[0]["payload"]["value"] == "tomorrow"


def test_booking_brain_change_yields_brain_estimate():
    record = _booking_modify(
        "booking-1",
        old_slots={},
        new_slots={},
        new_brain={
            "serviceType": "MOVE_OUT_CLEANING",
            "price": 703.13,
            "currency": "CHF",
            "needsPhotos": False,
        },
    )
    events = normalize_record(record)
    types = [e["type"] for e in events]
    assert "BrainEstimate" in types
    estimate = next(e for e in events if e["type"] == "BrainEstimate")
    assert estimate["payload"]["price"] == 703.13
    assert estimate["payload"]["currency"] == "CHF"


def test_unknown_record_returns_empty():
    assert normalize_record({"eventName": "INSERT", "dynamodb": {}}) == []
