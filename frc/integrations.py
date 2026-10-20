import math
import os
import httpx

from .models import Recipient, utcnow

HEADERS = {"User-Agent": "FoodRescueCoordinatorPilot/1.0 (manual coordinator lookup)"}


def fetch_directory(store, latitude, longitude, radius_km=10):
    latitude, longitude, radius_km = float(latitude), float(longitude), float(radius_km)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and 1 <= radius_km <= 20):
        raise ValueError("Use valid coordinates and a radius of 1–20 km.")
    key = f"osm:{latitude:.4f}:{longitude:.4f}:{radius_km:.1f}"
    cached = store.cache_get(key)
    if cached is not None:
        return cached
    query = f'''[out:json][timeout:20];nwr(around:{int(radius_km*1000)},{latitude},{longitude})["social_facility"~"^(food_bank|soup_kitchen)$"];out center tags 50;'''
    response = httpx.post("https://overpass-api.de/api/interpreter", data={"data": query}, headers=HEADERS, timeout=30)
    response.raise_for_status()
    result = []
    for element in response.json().get("elements", [])[:50]:
        tags = element.get("tags", {})
        center = element.get("center", element)
        address = " ".join(str(tags.get(k, "")) for k in ("addr:housenumber", "addr:street", "addr:city", "addr:postcode")).strip()
        osm_id = f"osm-{element['type']}-{element['id']}"
        result.append(Recipient(id=osm_id, name=tags.get("name", "Unnamed mapped facility"), address=address or None,
                                latitude=center.get("lat"), longitude=center.get("lon"), source="OpenStreetMap",
                                source_note=f"https://www.openstreetmap.org/{element['type']}/{element['id']} | retrieved {utcnow().isoformat()} | listed opening_hours: {tags.get('opening_hours','unknown')}"
                                ).model_dump(mode="json"))
    store.cache_put(key, result)
    return result


def import_directory(store, entries):
    imported = 0
    for entry in entries:
        recipient = Recipient.model_validate(entry)
        # Never let repeated directory imports overwrite confirmed profiles.
        if store.get("recipient", recipient.id):
            continue
        store.put("recipient", recipient)
        imported += 1
    return imported


def estimate_route(store, donation_id, recipient_id):
    donation_row = store.get("donation", donation_id)
    recipient_row = store.get("recipient", recipient_id)
    if not donation_row or not recipient_row:
        return {"status": "RECORD_NOT_FOUND"}
    donation, recipient = donation_row[0], recipient_row[0]
    if donation.is_demo != recipient.is_demo:
        return {"status": "DEMO_DATA_MISMATCH"}
    if any(v is None for v in (donation.latitude, donation.longitude, recipient.latitude, recipient.longitude)):
        return {"status": "NEEDS_COORDINATES", "reason": "Enter verified latitude/longitude for both locations in the forms."}
    key = os.getenv("ORS_API_KEY")
    if not key:
        return {"status": "ROUTING_NOT_CONFIGURED", "reason": "Coordinator can supply an estimate manually; no route value was invented."}
    cache_key = f"route:{donation.longitude}:{donation.latitude}:{recipient.longitude}:{recipient.latitude}"
    cached = store.cache_get(cache_key)
    if cached:
        return cached
    response = httpx.post("https://api.openrouteservice.org/v2/directions/driving-car/json",
                          headers={**HEADERS, "Authorization": key}, timeout=25,
                          json={"coordinates": [[donation.longitude, donation.latitude], [recipient.longitude, recipient.latitude]]})
    response.raise_for_status()
    summary = response.json()["routes"][0]["summary"]
    result = {"status": "ROUTE_ESTIMATE", "travel_minutes": math.ceil(summary["duration"] / 60),
              "distance_km": round(summary["distance"] / 1000, 2), "source": "openrouteservice",
              "evaluated_at": utcnow().isoformat(), "limitations": "Modeled driving estimate, not a live-traffic guarantee. Add loading and operational buffers."}
    store.cache_put(cache_key, result, 900)
    return result
