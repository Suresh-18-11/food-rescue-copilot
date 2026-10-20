import os
from datetime import datetime, timedelta
from uuid import uuid4

from .models import Donation, Recipient, utcnow, window_status


def get_selected(store, donation_id):
    row = store.get("donation", donation_id) if donation_id else None
    return row[0] if row else None


def profile_fresh(recipient, now):
    hours = float(os.getenv("PROFILE_MAX_AGE_HOURS", "24"))
    return bool(recipient.verified_at and timedelta(0) <= now - recipient.verified_at <= timedelta(hours=hours))


def discover(store, donation_id):
    donation = get_selected(store, donation_id)
    if not donation:
        return {"status": "DONATION_NOT_FOUND"}
    now = utcnow()
    candidates, needs_confirmation = [], []
    for recipient, _ in store.list("recipient"):
        if recipient.is_demo != donation.is_demo:
            continue
        item = recipient.model_dump(mode="json")
        item["receiving_window_status"] = window_status(recipient, now)
        item["profile_fresh"] = profile_fresh(recipient, now)
        if recipient.accepts_prepared_meals is False:
            continue
        if recipient.available_portions is not None and recipient.available_portions < donation.portions:
            continue
        if recipient.accepts_prepared_meals is not True or recipient.available_portions is None:
            needs_confirmation.append(item)
        else:
            candidates.append(item)
    return {"status": "SEARCH_COMPLETE", "donation_id": donation_id,
            "evaluated_at": now.isoformat(), "candidates": candidates,
            "needs_confirmation": needs_confirmation,
            "limitations": "Recorded acceptance/capacity filter only. Hours and profile freshness are labeled. No reservations, acceptance, volunteer, safety certification or delivery confirmation."}


def assess(store, donation_id, recipient_id, pickup_time, travel_minutes):
    donation = get_selected(store, donation_id)
    row = store.get("recipient", recipient_id)
    if not donation or not row:
        return {"status": "RECORD_NOT_FOUND"}
    recipient = row[0]
    if recipient.is_demo != donation.is_demo:
        return {"status": "DEMO_DATA_MISMATCH"}
    now = utcnow()
    if not donation.pickup_deadline or not donation.available_from or not donation.address:
        return {"status": "NEEDS_INFORMATION", "reason": "Donation needs pickup address, available-from time and deadline."}
    if donation.pickup_deadline <= now:
        return {"status": "EXPIRED", "reason": "Donation pickup deadline has passed."}
    if recipient.accepts_prepared_meals is None or recipient.available_portions is None:
        return {"status": "NEEDS_INFORMATION", "reason": "Recipient acceptance or available capacity is unknown."}
    if not recipient.accepts_prepared_meals or recipient.available_portions < donation.portions:
        return {"status": "RECIPIENT_INELIGIBLE"}
    if not profile_fresh(recipient, now):
        return {"status": "NEEDS_CONFIRMATION", "reason": "Recipient operational profile has not been recently confirmed by the coordinator."}
    if not recipient.receiving_start or not recipient.receiving_end:
        return {"status": "NEEDS_INFORMATION", "reason": "Recipient receiving hours are unknown."}
    try:
        pickup = datetime.fromisoformat(pickup_time.replace("Z", "+00:00"))
        if pickup.tzinfo is None or pickup.utcoffset() is None:
            raise ValueError("Pickup needs a timezone.")
        if type(travel_minutes) is not int or not 0 <= travel_minutes <= 1440:
            raise ValueError("Travel must be an integer from 0 to 1440 minutes.")
    except (ValueError, TypeError, AttributeError) as exc:
        return {"status": "INVALID_INPUT", "reason": str(exc)}
    if pickup <= now or pickup < donation.available_from or pickup > donation.pickup_deadline:
        return {"status": "INVALID_PICKUP", "reason": "Pickup must be future and inside the donor's available window."}
    arrival = pickup + timedelta(minutes=travel_minutes)
    if not recipient.receiving_start <= arrival < recipient.receiving_end:
        return {"status": "OUTSIDE_RECEIVING_HOURS", "estimated_arrival": arrival.isoformat()}
    return {"status": "PRELIMINARY_CHECKS_PASSED", "estimated_arrival": arrival.isoformat(),
            "limitations": "Uses coordinator-confirmed records and the supplied travel estimate. No reservation, recipient acceptance, volunteer assignment, or food-safety determination."}


def seed_demo(store):
    now = utcnow()
    donation_id = "demo-donation-" + uuid4().hex[:8]
    donation = Donation(id=donation_id, name="DEMO Restaurant", description="25 packaged vegetarian meals",
                        portions=25, address="DEMO pickup address", is_demo=True,
                        available_from=now + timedelta(minutes=15), pickup_deadline=now + timedelta(hours=4))
    recipient = Recipient(id="demo-recipient-" + uuid4().hex[:8], name="DEMO Kitchen", is_demo=True,
                          accepts_prepared_meals=True, available_portions=30, verified_at=now,
                          receiving_start=now + timedelta(hours=1), receiving_end=now + timedelta(hours=3))
    store.put("donation", donation)
    store.put("recipient", recipient)
    return donation, recipient, (now + timedelta(hours=2)).isoformat()
