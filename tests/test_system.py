import json
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from frc.agent import Agent
from frc.models import Donation, Recipient, utcnow, window_status
from frc.services import assess, discover, seed_demo
from frc.store import Store, Conflict
from frc.integrations import import_directory, estimate_route


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def scenario(store):
    return seed_demo(store)


def test_empty_by_default(store):
    assert store.list("donation") == []
    assert store.list("recipient") == []


@pytest.mark.parametrize("portions", [0, -1, "25", True])
def test_reject_invalid_portions(portions):
    with pytest.raises(ValidationError):
        Donation(id="d", name="Restaurant", description="Meals", portions=portions)


def test_timezone_required():
    with pytest.raises(ValidationError):
        Donation(id="d", name="Restaurant", description="Meals", portions=2, pickup_deadline="2027-01-01T12:00:00")


def test_unknown_profile_not_candidate(store):
    donor = Donation(id="d", name="Restaurant", description="Meals", portions=25)
    rec = Recipient(id="r", name="Public food bank", source="OpenStreetMap")
    store.put("donation", donor)
    store.put("recipient", rec)
    result = discover(store, "d")
    assert not result["candidates"]
    assert result["needs_confirmation"][0]["available_portions"] is None


@pytest.mark.parametrize("start,end,expected", [(None,None,"UNKNOWN"),(1,2,"UPCOMING"),(-1,1,"OPEN"),(0,1,"OPEN"),(-1,0,"EXPIRED"),(-2,-1,"EXPIRED")])
def test_window_boundaries(start, end, expected):
    now = utcnow()
    rec = Recipient(id="r", name="Kitchen", receiving_start=now+timedelta(hours=start) if start is not None else None,
                    receiving_end=now+timedelta(hours=end) if end is not None else None)
    assert window_status(rec, now) == expected


def test_assessment_and_no_reservation(store, scenario):
    donor, rec, pickup = scenario
    assert assess(store, donor.id, rec.id, pickup, 30)["status"] == "PRELIMINARY_CHECKS_PASSED"
    assert store.get("recipient", rec.id)[0].available_portions == 30


def test_arrival_at_close_is_rejected(store, scenario):
    donor, rec, _ = scenario
    pickup = (rec.receiving_end-timedelta(minutes=30)).isoformat()
    assert assess(store, donor.id, rec.id, pickup, 30)["status"] == "OUTSIDE_RECEIVING_HOURS"


def test_donor_not_ready(store, scenario):
    donor, rec, _ = scenario
    assert assess(store, donor.id, rec.id, utcnow().isoformat(), 30)["status"] == "INVALID_PICKUP"


def test_stale_profile(store, scenario):
    donor, rec, pickup = scenario
    stale = Recipient.model_validate({**rec.model_dump(), "verified_at": utcnow()-timedelta(days=10)})
    store.put("recipient", stale, 1)
    assert assess(store, donor.id, rec.id, pickup, 30)["status"] == "NEEDS_CONFIRMATION"


def test_demo_real_never_match(store, scenario):
    donor, rec, pickup = scenario
    real = Donation.model_validate({**donor.model_dump(), "is_demo": False})
    store.put("donation", real, 1)
    assert not discover(store, donor.id)["candidates"]
    assert assess(store, donor.id, rec.id, pickup, 30)["status"] == "DEMO_DATA_MISMATCH"


def test_approval_decline_persistence(store, scenario):
    donor, _, _ = scenario
    deadline = (utcnow()+timedelta(hours=5)).isoformat()
    assert store.propose("req", donor.id, deadline)["status"] == "AWAITING_APPROVAL"
    assert store.get("donation", donor.id)[0].pickup_deadline == donor.pickup_deadline
    assert store.decide("req", False)["status"] == "DECLINED"
    assert Store(store.path).propose("req", donor.id, deadline)["status"] == "DECLINED"
    assert store.get("donation", donor.id)[0].pickup_deadline == donor.pickup_deadline


def test_update_replay_and_newer_state(store, scenario):
    donor, _, _ = scenario
    a = (utcnow()+timedelta(hours=5)).isoformat()
    b = (utcnow()+timedelta(hours=6)).isoformat()
    store.propose("a", donor.id, a)
    assert store.decide("a", True)["status"] == "UPDATED_AND_VERIFIED"
    assert Store(store.path).propose("a", donor.id, a)["status"] == "REPLAYED_AND_VERIFIED"
    store.propose("b", donor.id, b)
    store.decide("b", True)
    assert store.propose("a", donor.id, a)["status"] == "REPLAYED_STATE_CHANGED"
    assert store.get("donation", donor.id)[0].pickup_deadline.isoformat() == b


def test_request_conflict(store, scenario):
    donor, _, _ = scenario
    store.propose("a", donor.id, (utcnow()+timedelta(hours=5)).isoformat())
    assert store.propose("a", donor.id, (utcnow()+timedelta(hours=6)).isoformat())["status"] == "REQUEST_ID_CONFLICT"


def test_stale_approval_cannot_overwrite_form_edit(store, scenario):
    donor, _, _ = scenario
    store.propose("a", donor.id, (utcnow()+timedelta(hours=5)).isoformat())
    edited = Donation.model_validate({**donor.model_dump(), "description": "Changed meals"})
    store.put("donation", edited, 1)
    assert store.decide("a", True)["status"] == "RECORD_CHANGED"
    assert store.get("donation", donor.id)[0].description == "Changed meals"
    assert store.get("donation", donor.id)[0].pickup_deadline == donor.pickup_deadline


def test_session_revision_conflict(store):
    revision = store.save_session("s", {"phase": "IDLE"}, 0)
    store.save_session("s", {"phase": "MODEL"}, revision)
    with pytest.raises(Conflict):
        store.save_session("s", {"phase": "TOOLS"}, revision)


def test_record_revision_conflict(store, scenario):
    donor, _, _ = scenario
    store.put("donation", donor, 1)
    with pytest.raises(Conflict):
        store.put("donation", donor, 1)


def test_import_does_not_overwrite_confirmed(store):
    rec = Recipient(id="osm-node-1", name="Confirmed", accepts_prepared_meals=True, available_portions=30, verified_at=utcnow())
    store.put("recipient", rec)
    assert import_directory(store, [Recipient(id=rec.id, name="Directory").model_dump(mode="json")]) == 0
    assert store.get("recipient", rec.id)[0].available_portions == 30


def test_usage_limit(store):
    store.reserve_call(1)
    with pytest.raises(RuntimeError):
        store.reserve_call(1)


def test_missing_coordinates_no_route_invented(store, scenario):
    donor, rec, _ = scenario
    assert estimate_route(store, donor.id, rec.id)["status"] == "NEEDS_COORDINATES"


class FakeResponses:
    def __init__(self, items):
        self.items = iter(items)
        self.requests = []
    def create(self, **kwargs):
        self.requests.append(kwargs)
        return next(self.items)


def response(response_id, name=None, arguments=None, text="Done"):
    output = [NS(type="function_call", call_id="call-1", name=name, arguments=json.dumps(arguments or {}))] if name else []
    return NS(id=response_id, status="completed", output=output, output_text=text)


def test_agent_discovery_checkpoint(store, scenario):
    donor, _, _ = scenario
    fake = FakeResponses([response("r1", "find_recipients"), response("r2", text="Preliminary candidates")])
    agent = Agent(store, "s", NS(responses=fake))
    agent.select(donor.id)
    agent.begin("Find recipients")
    agent.pump()
    resumed = Agent(Store(store.path), "s", NS(responses=fake))
    assert resumed.state["phase"] == "IDLE"
    assert resumed.state["selected"] == donor.id
    assert resumed.state["traces"][0]["result"]["candidates"]
    assert "donation_id" not in fake.requests[0]["tools"][1]["parameters"]["properties"]


def test_agent_recovers_after_committed_write(store, scenario):
    donor, _, _ = scenario
    deadline = (utcnow()+timedelta(hours=5)).isoformat()
    fake = FakeResponses([response("r1", "request_deadline_update", {"pickup_deadline": deadline})])
    agent = Agent(store, "recovery", NS(responses=fake))
    agent.select(donor.id)
    agent.begin("Save this deadline")
    agent.pump()
    assert agent.state["phase"] == "APPROVAL"
    request_id = agent.state["waiting"]
    # Commit update but deliberately do not save the agent checkpoint.
    assert store.decide(request_id, True)["status"] == "UPDATED_AND_VERIFIED"
    resumed = Agent(Store(store.path), "recovery", NS(responses=FakeResponses([response("r2")])))
    resumed.resolve(True)  # Already-decided request must not write again.
    resumed.pump()
    assert resumed.state["phase"] == "IDLE"
    assert resumed.state["traces"][0]["result"]["status"] == "REPLAYED_AND_VERIFIED"
    assert len([e for e in store.audit() if e["event"] == "APPROVED"]) == 1


def test_scope_mismatch_blocks_tool(store, scenario):
    donor, _, _ = scenario
    agent = Agent(store, "s")
    agent.select(donor.id)
    result = agent.execute({"scope": "different", "name": "request_deadline_update", "arguments": {"pickup_deadline": "2027-01-01T00:00:00Z"}, "request_id": "bad"})
    assert result["status"] == "DONATION_SELECTION_REQUIRED_OR_CHANGED"
    assert store.request("bad") is None


def test_directory_parser_preserves_unknowns(store, monkeypatch):
    import frc.integrations as integrations
    calls = []
    def fake_post(url, **kwargs):
        calls.append(url)
        assert "food_bank" in kwargs["data"]["data"]
        return NS(raise_for_status=lambda: None, json=lambda: {"elements": [
            {"type": "node", "id": 123, "lat": 40.7, "lon": -74.0,
             "tags": {"name": "Mapped organization", "opening_hours": "Mo-Fr 09:00-17:00"}}
        ]})
    monkeypatch.setattr(integrations.httpx, "post", fake_post)
    rows = integrations.fetch_directory(store, 40.7, -74.0, 3)
    integrations.fetch_directory(store, 40.7, -74.0, 3)
    assert len(calls) == 1
    assert rows[0]["available_portions"] is None
    assert rows[0]["accepts_prepared_meals"] is None
    assert rows[0]["receiving_start"] is None
    assert "openstreetmap.org/node/123" in rows[0]["source_note"]


def test_route_units_and_cache(store, scenario, monkeypatch):
    import frc.integrations as integrations
    donor, rec, _ = scenario
    store.put("donation", Donation.model_validate({**donor.model_dump(), "latitude": 40.7, "longitude": -74.0}), 1)
    store.put("recipient", Recipient.model_validate({**rec.model_dump(), "latitude": 40.8, "longitude": -74.1}), 1)
    monkeypatch.setenv("ORS_API_KEY", "unit-test-placeholder")
    calls = []
    def fake_post(url, **kwargs):
        calls.append(url)
        assert kwargs["json"]["coordinates"] == [[-74.0, 40.7], [-74.1, 40.8]]
        return NS(raise_for_status=lambda: None, json=lambda: {"routes": [{"summary": {"duration": 65, "distance": 1250}}]})
    monkeypatch.setattr(integrations.httpx, "post", fake_post)
    result = integrations.estimate_route(store, donor.id, rec.id)
    assert result["travel_minutes"] == 2
    assert result["distance_km"] == 1.25
    integrations.estimate_route(store, donor.id, rec.id)
    assert len(calls) == 1
