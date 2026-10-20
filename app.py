import hmac
import json
import os
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv

from frc.agent import Agent
from frc.integrations import fetch_directory, import_directory
from frc.models import Donation, Recipient, utcnow
from frc.services import seed_demo
from frc.store import Store, Conflict

load_dotenv(Path(__file__).parent / ".env")
st.set_page_config(page_title="Food Rescue Copilot", page_icon="🥕", layout="wide")


@st.cache_resource
def login_limiter():
    return {"lock": threading.Lock(), "failed": 0, "until": 0.0}


def authenticate():
    if os.getenv("LOCAL_DEV") == "1" and not os.getenv("RENDER"):
        st.sidebar.warning("Local development: login bypassed. Do not expose this mode online.")
        return True
    configured = os.getenv("APP_PASSWORD", "")
    if len(configured) < 16:
        st.error("Set APP_PASSWORD to a strong password of at least 16 characters. See README. For localhost only, LOCAL_DEV=1 is available.")
        return False
    if st.session_state.get("authenticated"):
        if st.sidebar.button("Sign out"):
            st.session_state.clear()
            st.rerun()
        return True
    st.title("Food Rescue Copilot")
    st.caption("Private coordinator workspace")
    limiter = login_limiter()
    with st.form("sign_in"):
        password = st.text_input("Coordinator password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        with limiter["lock"]:
            if time.monotonic() < limiter["until"]:
                st.error("Too many unsuccessful attempts. Wait one minute.")
            elif hmac.compare_digest(password.encode(), configured.encode()):
                limiter["failed"] = 0
                st.session_state.authenticated = True
                st.rerun()
            else:
                limiter["failed"] += 1
                if limiter["failed"] >= 5:
                    limiter["until"] = time.monotonic() + 60
                    limiter["failed"] = 0
                st.error("Invalid password.")
    return False


if not authenticate():
    st.stop()

store = Store(Path(os.getenv("DATA_DIR", "./data")) / "food_rescue_v1.db")
st.sidebar.title("🥕 Food Rescue")
page = st.sidebar.radio("Workspace", ["Overview", "Donations", "Recipients", "Public directory", "Copilot", "Audit"])
st.sidebar.caption("Coordinator pilot • prepared-meal matching • no dispatch or bookings")
st.title(page)


def value(text):
    return text.strip() or None


def number(text):
    return float(text) if text.strip() else None


def basic_fields(record, prefix):
    name = st.text_input("Name", value=record.name if record else "", key=prefix + "name", max_chars=200)
    address = st.text_input("Address", value=record.address or "" if record else "", key=prefix + "address", max_chars=500)
    c1, c2 = st.columns(2)
    lat = c1.text_input("Latitude (optional)", value=str(record.latitude) if record and record.latitude is not None else "", key=prefix + "lat")
    lon = c2.text_input("Longitude (optional)", value=str(record.longitude) if record and record.longitude is not None else "", key=prefix + "lon")
    demo = st.checkbox("Synthetic demonstration record", value=record.is_demo if record else False, key=prefix + "demo")
    return name, address, lat, lon, demo


def revision_for_form(kind, record_id, revision):
    key = f"revision:{kind}:{record_id}"
    if key not in st.session_state:
        st.session_state[key] = revision
    return key, st.session_state[key]


try:
    if page == "Overview":
        st.markdown("### From surplus food to a supported preliminary decision")
        st.write("Enter donor offers, record confirmed recipient information, discover candidates, and assess timing. The copilot can propose deadline changes; you approve them before saving.")
        a, b = st.columns(2)
        a.metric("Saved donations", len(store.list("donation")))
        b.metric("Saved recipients / directory leads", len(store.list("recipient")))
        st.info("Public listings are leads, not confirmed partners. Unknown capacity stays unknown. This app does not contact organizations, determine food safety, reserve capacity, assign volunteers, or confirm deliveries.")
        with st.expander("Optional synthetic walkthrough"):
            st.write("Creates clearly labeled demo records. Demo and real records are never matched together.")
            if st.button("Create fresh demo scenario"):
                donor, recipient, pickup = seed_demo(store)
                st.session_state.demo_instructions = {"donation_id": donor.id, "request": f"Assess recipient {recipient.id}. Pickup is {pickup}; travel estimate is 30 minutes."}
            if st.session_state.get("demo_instructions"):
                st.json(st.session_state.demo_instructions)
        st.caption("Start with Donations → Recipients (or Public directory) → Copilot. All ISO datetimes require a timezone, e.g. 2026-10-19T14:00:00-04:00.")

    elif page == "Donations":
        records = {r.id: (r, rev) for r, rev in store.list("donation")}
        chosen = st.selectbox("Donation to edit", ["Create new"] + list(records), format_func=lambda x: x if x == "Create new" else f"{records[x][0].name} · {x}")
        record, revision = records.get(chosen, (None, None))
        rid = record.id if record else st.session_state.setdefault("new_donation_id", "don-" + uuid4().hex[:12])
        revision_key, expected = revision_for_form("donation", rid, revision)
        now = utcnow()
        with st.form("donation:" + rid):
            name, address, lat, lon, demo = basic_fields(record, rid)
            description = st.text_area("Food description", value=record.description if record else "", max_chars=2000)
            portions = st.number_input("Prepared-meal portions", min_value=1, max_value=100000, value=record.portions if record else 25, step=1)
            ready = st.text_input("Available from (ISO datetime with timezone; blank = unknown)", value=record.available_from.isoformat() if record and record.available_from else "")
            deadline = st.text_input("Pickup deadline (ISO datetime with timezone; blank = unknown)", value=record.pickup_deadline.isoformat() if record and record.pickup_deadline else "")
            save = st.form_submit_button("Save donation")
        if save:
            donation = Donation(id=rid, name=name, address=value(address), latitude=number(lat), longitude=number(lon),
                                is_demo=demo, description=description, portions=int(portions), available_from=value(ready), pickup_deadline=value(deadline))
            if donation.pickup_deadline and donation.pickup_deadline <= now:
                raise ValueError("Use a future deadline or leave it unknown.")
            store.put("donation", donation, expected)
            st.session_state.pop(revision_key, None)
            st.session_state.pop("new_donation_id", None)
            st.success("Donation saved. Select it in Copilot.")
        if record:
            st.caption(f"Record ID: {rid} · revision {revision}")

    elif page == "Recipients":
        records = {r.id: (r, rev) for r, rev in store.list("recipient")}
        chosen = st.selectbox("Recipient to edit", ["Create new"] + list(records), format_func=lambda x: x if x == "Create new" else f"{records[x][0].name} · {x}")
        record, revision = records.get(chosen, (None, None))
        rid = record.id if record else st.session_state.setdefault("new_recipient_id", "rec-" + uuid4().hex[:12])
        revision_key, expected = revision_for_form("recipient", rid, revision)
        with st.form("recipient:" + rid):
            name, address, lat, lon, demo = basic_fields(record, rid)
            options = ["Unknown", "Yes", "No"]
            acceptance = st.selectbox("Accepts prepared meals", options, index=0 if not record or record.accepts_prepared_meals is None else (1 if record.accepts_prepared_meals else 2))
            capacity_known = st.checkbox("Available portion capacity is known", value=bool(record and record.available_portions is not None))
            capacity = st.number_input("Available additional portions", min_value=0, max_value=100000, value=record.available_portions if record and record.available_portions is not None else 0, step=1)
            start = st.text_input("Receiving start (dated ISO datetime with timezone)", value=record.receiving_start.isoformat() if record and record.receiving_start else "")
            end = st.text_input("Receiving end (dated ISO datetime with timezone)", value=record.receiving_end.isoformat() if record and record.receiving_end else "")
            confirmed = st.checkbox("I have just confirmed these operational details with the organization (or this is a demo)", value=False)
            note = st.text_area("Source / confirmation notes", value=record.source_note if record else "", max_chars=2000)
            save = st.form_submit_button("Save recipient")
        if save:
            recipient = Recipient(id=rid, name=name, address=value(address), latitude=number(lat), longitude=number(lon), is_demo=demo,
                                  accepts_prepared_meals={"Unknown": None, "Yes": True, "No": False}[acceptance],
                                  available_portions=int(capacity) if capacity_known else None, receiving_start=value(start), receiving_end=value(end),
                                  verified_at=utcnow() if confirmed else None,
                                  source=record.source if record else "coordinator", source_note=note)
            store.put("recipient", recipient, expected)
            st.session_state.pop(revision_key, None)
            st.session_state.pop("new_recipient_id", None)
            st.success("Recipient saved. Profiles need recent confirmation to pass assessment.")
        if record:
            st.caption(f"Record ID: {rid} · revision {revision} · source: {record.source}")

    elif page == "Public directory":
        st.write("Search mapped food banks and soup kitchens. Coverage varies. Importing creates unconfirmed leads, with unknown acceptance and capacity.")
        with st.form("directory_search"):
            a, b, c = st.columns(3)
            lat = a.number_input("Center latitude", value=26.3683, min_value=-90.0, max_value=90.0, format="%.5f")
            lon = b.number_input("Center longitude", value=-80.1289, min_value=-180.0, max_value=180.0, format="%.5f")
            radius = c.slider("Radius (km)", 1, 20, 10)
            search = st.form_submit_button("Search OpenStreetMap")
        if search:
            with st.spinner("Querying public directory…"):
                st.session_state.directory_results = fetch_directory(store, lat, lon, radius)
        results = st.session_state.get("directory_results", [])
        if results:
            st.dataframe([{k: r[k] for k in ("id", "name", "address", "latitude", "longitude")} for r in results], hide_index=True)
            selected = st.multiselect("Select leads to import", [r["id"] for r in results], format_func=lambda x: next(r["name"] for r in results if r["id"] == x))
            if st.button("Import selected leads", disabled=not selected):
                count = import_directory(store, [r for r in results if r["id"] in selected])
                st.success(f"Imported {count} new leads. Existing confirmed records were preserved.")
        elif search:
            st.info("No mapped results in this area. This does not establish that no organizations exist. You can add a recipient manually.")
        st.markdown("Data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), ODbL. Public-directory results retain their source links. Routing, when configured, uses [openrouteservice](https://openrouteservice.org/) / OpenStreetMap data.")

    elif page == "Copilot":
        if "session_choice" not in st.session_state:
            st.session_state.session_choice = "session-" + secrets.token_hex(8)
        with st.form("load_session"):
            session_id = st.text_input("Conversation ID (keep this to resume later)", value=st.session_state.session_choice, max_chars=100)
            load = st.form_submit_button("Open conversation")
        if load:
            if not session_id.strip():
                raise ValueError("Conversation ID cannot be empty.")
            st.session_state.session_choice = session_id.strip()
        agent = Agent(store, st.session_state.session_choice)
        state = agent.state
        st.caption(f"Conversation: {agent.session_id} · phase: {state['phase']}")
        records = {r.id: r for r, _ in store.list("donation")}
        options = [None] + list(records)
        current = state["selected"] if state["selected"] in records else None
        chosen = st.selectbox("Selected donation", options, index=options.index(current),
                              format_func=lambda x: "Select a donation" if x is None else f"{'DEMO · ' if records[x].is_demo else ''}{records[x].name} · {x}",
                              disabled=state["phase"] != "IDLE")
        if chosen != current:
            agent.select(chosen)
            st.rerun()
        for message in state["messages"]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        if state["error"]:
            st.error(state["error"])
        if state["phase"] == "APPROVAL":
            request = store.request(state["waiting"])
            st.warning("Approval required: change the saved pickup deadline")
            st.json({"request_id": request["id"], "requested_change": json.loads(request["payload"]), "donation_revision_when_proposed": request["base_revision"]})
            a, b = st.columns(2)
            if a.button("Approve exact change", type="primary"):
                agent.resolve(True)
                st.rerun()
            if b.button("Decline"):
                agent.resolve(False)
                st.rerun()
        if state["phase"] not in ("IDLE", "APPROVAL"):
            st.info("Pending work is saved. Resume explicitly; browser refresh does not automatically repeat API calls.")
            if st.button("Run / resume pending turn", type="primary"):
                if not os.getenv("OPENAI_API_KEY"):
                    st.error("Set OPENAI_API_KEY in server environment or local .env.")
                else:
                    with st.spinner("Working…"):
                        agent.pump()
                    st.rerun()
        if state["phase"] != "IDLE" and st.button("Cancel pending turn"):
            agent.cancel_turn()
            st.rerun()
        if not os.getenv("OPENAI_API_KEY"):
            st.info("Model chat requires your OpenAI API key. Donation forms, directory and audit remain available without it.")
        message = st.chat_input("Ask to discover recipients, assess a schedule, or propose a deadline change", disabled=state["phase"] != "IDLE" or not os.getenv("OPENAI_API_KEY"))
        if message:
            agent.begin(message)
            st.rerun()
        with st.expander("Tool activity and saved results"):
            for trace in state["traces"][-20:]:
                st.json(trace)
        st.caption("Shared single-coordinator workspace. All holders of the app password can access its records and conversations.")

    elif page == "Audit":
        events = store.audit()
        st.dataframe(events, hide_index=True, width="stretch")
        st.download_button("Download latest audit events", json.dumps(events, indent=2), "audit-events.json", "application/json")
        st.caption("Latest 300 events. Times are UTC. This local audit table is not tamper-proof; protect the database and backups.")

except Conflict as exc:
    st.error(str(exc))
    st.info("Open a fresh browser session or choose a new conversation ID. Reopen the record form to refresh its edit revision.")
except Exception as exc:
    # Operational forms may contain addresses, but never show API keys/HTTP bodies.
    if isinstance(exc, ValueError):
        st.error(str(exc))
    else:
        st.error(f"{type(exc).__name__}: operation failed. Check service configuration/network. No success is assumed; inspect saved state and audit.")
