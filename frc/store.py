import json
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from .models import Donation, Recipient, utcnow


class Conflict(RuntimeError):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, donation_id TEXT NOT NULL, payload TEXT NOT NULL,
                    base_revision INTEGER NOT NULL, decision TEXT NOT NULL,
                    result TEXT);
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
                    event TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (
                    day TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, request_id, event, details):
        db.execute("INSERT INTO audit(request_id,event,details,created_at) VALUES(?,?,?,?)",
                   (request_id, event, json.dumps(details), utcnow().isoformat()))

    def get(self, kind, record_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        if not row:
            return None
        cls = Donation if kind == "donation" else Recipient
        return cls.model_validate_json(row["payload"]), row["revision"]

    def list(self, kind):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM records WHERE kind=? ORDER BY rowid DESC", (kind,)).fetchall()
        cls = Donation if kind == "donation" else Recipient
        return [(cls.model_validate_json(r["payload"]), r["revision"]) for r in rows]

    def put(self, kind, record, expected_revision=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM records WHERE kind=? AND id=?", (kind, record.id)).fetchone()
            if row:
                if expected_revision != row["revision"]:
                    raise Conflict("Record changed. Reload before editing.")
                db.execute("UPDATE records SET payload=?,revision=revision+1 WHERE kind=? AND id=?",
                           (record.model_dump_json(), kind, record.id))
            else:
                if expected_revision is not None:
                    raise Conflict("Record no longer exists.")
                db.execute("INSERT INTO records(kind,id,payload) VALUES(?,?,?)", (kind, record.id, record.model_dump_json()))
            self.event(db, "form:" + uuid4().hex, "RECORD_SAVED", {"kind": kind, "id": record.id})

    def session(self, session_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return (json.loads(row["payload"]), row["revision"]) if row else (None, 0)

    def save_session(self, session_id, state, revision):
        with self.connect() as db:
            if revision == 0:
                try:
                    db.execute("INSERT INTO sessions VALUES(?,?,1)", (session_id, json.dumps(state)))
                except sqlite3.IntegrityError as exc:
                    raise Conflict("Session is open elsewhere. Reload.") from exc
            else:
                cursor = db.execute("UPDATE sessions SET payload=?,revision=revision+1 WHERE id=? AND revision=?",
                                    (json.dumps(state), session_id, revision))
                if cursor.rowcount != 1:
                    raise Conflict("Session changed in another tab. Reload.")
        return revision + 1

    def propose(self, request_id, donation_id, deadline):
        payload = json.dumps({"donation_id": donation_id, "pickup_deadline": deadline}, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if previous:
                if previous["payload"] != payload:
                    return {"status": "REQUEST_ID_CONFLICT"}
                if previous["result"]:
                    result = json.loads(previous["result"])
                    if previous["decision"] == "APPROVED":
                        row = db.execute("SELECT payload FROM records WHERE kind='donation' AND id=?", (donation_id,)).fetchone()
                        current = Donation.model_validate_json(row["payload"]) if row else None
                        expected = Donation.model_validate_json(result["saved_donation"])
                        result = {"status": "REPLAYED_AND_VERIFIED" if current and current.pickup_deadline == expected.pickup_deadline else "REPLAYED_STATE_CHANGED",
                                  "request_id": request_id, "replayed": True,
                                  "current_deadline": current.pickup_deadline.isoformat() if current and current.pickup_deadline else None}
                    return result
                return {"status": "AWAITING_APPROVAL", "request_id": request_id}
            row = db.execute("SELECT * FROM records WHERE kind='donation' AND id=?", (donation_id,)).fetchone()
            if not row:
                return {"status": "DONATION_NOT_FOUND"}
            original = Donation.model_validate_json(row["payload"])
            try:
                changed = Donation.model_validate({**original.model_dump(), "pickup_deadline": deadline})
                if not changed.pickup_deadline or changed.pickup_deadline <= utcnow():
                    raise ValueError("Deadline must be in the future.")
            except ValueError as exc:
                return {"status": "INVALID_INPUT", "reason": str(exc)}
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,NULL)",
                       (request_id, donation_id, payload, row["revision"], "PENDING"))
            self.event(db, request_id, "PROPOSED", {"donation_id": donation_id, "deadline": deadline, "base_revision": row["revision"]})
            return {"status": "AWAITING_APPROVAL", "request_id": request_id}

    def request(self, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def decide(self, request_id, approve):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not request:
                return {"status": "REQUEST_NOT_FOUND"}
            if request["decision"] != "PENDING":
                return {"status": "ALREADY_DECIDED", "decision": request["decision"]}
            data = json.loads(request["payload"])
            decision = "DECLINED"
            result = {"status": "DECLINED", "reason": "Coordinator declined; no donation update ran."}
            if approve:
                row = db.execute("SELECT * FROM records WHERE kind='donation' AND id=?", (request["donation_id"],)).fetchone()
                if not row or row["revision"] != request["base_revision"]:
                    decision = "CONFLICT"
                    result = {"status": "RECORD_CHANGED", "reason": "Donation changed since proposal. Request a fresh update."}
                else:
                    try:
                        original = Donation.model_validate_json(row["payload"])
                        changed = Donation.model_validate({**original.model_dump(), "pickup_deadline": data["pickup_deadline"]})
                        if changed.pickup_deadline <= utcnow():
                            raise ValueError("Deadline is no longer in the future.")
                        db.execute("UPDATE records SET payload=?,revision=revision+1 WHERE kind='donation' AND id=?",
                                   (changed.model_dump_json(), request["donation_id"]))
                        saved = db.execute("SELECT payload FROM records WHERE kind='donation' AND id=?", (request["donation_id"],)).fetchone()
                        if Donation.model_validate_json(saved["payload"]).pickup_deadline != changed.pickup_deadline:
                            raise RuntimeError("Verification failed; transaction rolled back.")
                        decision = "APPROVED"
                        result = {"status": "UPDATED_AND_VERIFIED", "request_id": request_id, "replayed": False,
                                  "pickup_deadline": changed.pickup_deadline.isoformat(), "saved_donation": changed.model_dump_json()}
                    except ValueError as exc:
                        decision = "INVALID"
                        result = {"status": "INVALID_INPUT", "reason": str(exc)}
            db.execute("UPDATE requests SET decision=?,result=? WHERE id=?", (decision, json.dumps(result), request_id))
            self.event(db, request_id, decision, {"status": result["status"], "donation_id": request["donation_id"]})
        return result

    def audit(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT 300")]

    def reserve_call(self, limit):
        day = utcnow().date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO usage VALUES(?,0)", (day,))
            calls = db.execute("SELECT calls FROM usage WHERE day=?", (day,)).fetchone()[0]
            if calls >= limit:
                raise RuntimeError("Daily model-call limit reached. Forms and records still work.")
            db.execute("UPDATE usage SET calls=calls+1 WHERE day=?", (day,))

    def cache_get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row["payload"]) if row and row["expires_at"] > utcnow().isoformat() else None

    def cache_put(self, key, value, seconds=3600):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)",
                       (key, json.dumps(value), (utcnow() + timedelta(seconds=seconds)).isoformat()))
