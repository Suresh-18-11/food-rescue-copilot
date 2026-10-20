# Food Rescue Copilot — deployment edition

A complete, standalone Python web application for a single food-rescue coordinator.
It discovers preliminary recipient candidates, checks proposed delivery schedules,
and handles approved pickup-deadline updates with verification, audit history,
idempotency and conversation checkpoints.

This is a deployable coordinator pilot, not a completed food-rescue marketplace.
No volunteer dispatch, external messaging, bookings, reservations, recipient acceptance,
food-safety certification or delivery completion is implemented.

## 1. Run on your Mac

Extract the ZIP into a NEW folder. Keep your existing learning project unchanged.
Open this new folder in VS Code, then use its terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Open `.env` and configure:

```dotenv
OPENAI_API_KEY=your_OpenAI_API_key
OPENAI_MODEL=gpt-5-mini
APP_PASSWORD=choose_a_strong_unique_password_of_at_least_16_characters
LOCAL_DEV=0
DATA_DIR=./data
```

Use your actual key and password; the examples are placeholders. Never upload `.env`.
OpenAI API billing is separate from a ChatGPT subscription. API/model access depends
on your account. The model name is configurable.

```bash
python -m pytest -q
python -m streamlit run app.py --server.address=127.0.0.1
```

Open http://localhost:8501 and sign in with APP_PASSWORD.

You can open the forms and directory without an OpenAI key. Chat stays disabled until
a key is configured. For a localhost-only walkthrough you may set LOCAL_DEV=1 to bypass
login; this bypass is explicitly disabled when the RENDER environment variable exists.
Use LOCAL_DEV=0 for any deployment. Python 3.12 is the tested deployment runtime.

## 2. First successful walkthrough

1. Overview → expand **Optional synthetic walkthrough** → **Create fresh demo scenario**.
2. Copy the printed donation ID and assessment request.
3. Copilot → note the conversation ID (or open a named one).
4. Select the demo donation in the dropdown. Selection is enforced by application code;
   the model tools do not accept a donation ID argument.
5. Ask: **Find recipients for the selected donation.**
6. Click **Run / resume pending turn**. Read tool activity and the reply.
7. Paste the printed assessment request; click Run / resume again.
8. Expect PRELIMINARY_CHECKS_PASSED. This does not book a delivery.
9. Ask to save a specific future deadline with an ISO timezone, such as the next day's
   datetime you choose. Run the turn. Approve or decline in the browser.
10. After a decision, click Run / resume to return the verified result to the model.

All sample records are marked DEMO and only match other demo records. No sample data is
loaded on startup. Refreshing a page does not automatically repeat model API calls.

## 3. Use actual data

**Donations:** a coordinator enters the donor's actual offer: description, portions,
address, availability and deadline. Blank times remain unknown.

**Public directory:** searches OpenStreetMap's mapped food banks and soup kitchens within
1–20 km of coordinates you choose. Boca Raton is the default search center, not a coverage
guarantee. Search is cached for one hour and limited to 50 results. Review and import leads.
No result means no mapped result, not no organization. Names/locations/opening_hours are
public listings and may be incomplete or outdated. Attribution and source URLs are retained.

**Recipient profiles:** open an imported lead under Recipients, confirm its operational
details with the organization yourself, and record prepared-meal acceptance, additional
portion capacity and a specific dated receiving window. Tick the confirmation checkbox
only after confirmation. Editing without confirmation clears the verification timestamp.
Unknown acceptance/capacity stays unknown and is shown separately from capacity candidates.
Profiles older than PROFILE_MAX_AGE_HOURS (default 24) cannot pass an assessment.
This timestamp is a coordinator assertion, not independent verification.

**Coordinates:** enter known latitude/longitude for both locations. Address text is not
automatically geocoded in this version. Coordinate pairs are validated, but correctness
must be checked by the coordinator.

**Routing (optional):** create an openrouteservice API key at https://openrouteservice.org/
and set ORS_API_KEY on the server. Ask the copilot to estimate travel to a saved recipient.
The integration uses driving-car directions, caches estimates for 15 minutes, and rounds
duration upward to whole minutes. It is not a live-traffic guarantee and does not include
loading or additional operational buffers. Without a key or coordinates, no value is
invented; you may supply a realistic estimate manually. Observe provider quotas/terms.

Public APIs do not supply live donor inventory, verified daily recipient capacity or
volunteer availability. These require participating organizations or an authorized
partner integration. Do not describe public listings as confirmed partnerships.

## 4. Deploy on Render

The supplied `render.yaml` uses a PAID Starter web service plus a 1 GB persistent disk.
Review current pricing in Render before creating it. This package is not deployed yet.

1. Create a new private GitHub repository for THIS folder (the public-apis repository is
   an API catalog, not this application's source repository).
2. Commit the source, including render.yaml, requirements files and .python-version.
   Do not commit .env, database files, keys or backups. The supplied .gitignore excludes them.
3. In Render, choose New → Blueprint and connect that repository.
4. Review the paid service/disk settings. Supply OPENAI_API_KEY when prompted.
5. Render generates APP_PASSWORD. View it in the service's environment settings to sign in,
   or replace it with your own long unique password. Do not share it broadly.
6. Confirm DATA_DIR=/var/data and the disk mount path is /var/data. Only that path persists.
7. Deploy. Health endpoint: /_stcore/health. Open the Render service URL and sign in.
8. Create one donor/recipient and verify they survive a service restart. Check approvals,
   rejection, audit history, and conversation resume on the hosted app.

Manual Render setup, if you do not use Blueprint:

```text
Runtime: Python 3.12.14
Build: pip install -r requirements.txt
Start: python -m streamlit run app.py --server.address=0.0.0.0 --server.port=$PORT
Persistent disk: /var/data
DATA_DIR=/var/data
APP_PASSWORD=<strong password, >=16 characters>
OPENAI_API_KEY=<your key>
OPENAI_MODEL=gpt-5-mini
LOCAL_DEV=0
```

Add ORS_API_KEY only if using routing. Do not put secrets in Git or frontend JavaScript.
Render's default filesystem is ephemeral. A free web service without a disk is unsuitable
for this SQLite application's persistent data. This SQLite deployment is one service
instance; do not horizontally scale it. Move to managed PostgreSQL and managed identities
before a multi-organization rollout.

## 5. Optional Docker

```bash
docker build -t food-rescue-copilot .
docker run --env-file .env -e LOCAL_DEV=0 -p 127.0.0.1:8501:8501 -v rescue-data:/app/data food-rescue-copilot
```

The image uses a non-root user. The named volume retains SQLite data. Docker configuration
is supplied; Docker execution is not part of the local Python validation unless noted in
VALIDATION.md.

## 6. Recovery, approvals, and boundaries

- Conversations persist in SQLite and refer to OpenAI response IDs. They depend on provider
  retention/access; the local transcript is not automatically replayed if a remote response
  expires. Cancel the turn and start a new model context if needed, after reviewing audit.
- Pending tool calls retain original request IDs and selected donation scope.
- Deadline updates require an on-screen decision. The immutable request payload binds the
  request ID to one donation and deadline. Reusing it with different inputs is rejected.
- Approval checks the donation revision again; a changed record invalidates the proposal.
- The mutation, durable result and approval audit commit together in one SQLite transaction.
- A crash after the commit but before session checkpointing replays the result without
  applying the update again. Current state is checked separately; later edits are preserved.
- The agent has three tool-calling rounds per user turn, a 30-second request timeout,
  up to three model-call attempts, and MAX_DAILY_MODEL_CALLS across all conversations.
  The daily counter counts attempts, not dollars; also set provider billing limits.
- Cancel pending turn does not undo saved updates. It resets model context and preserves
  the local history. Form edits are audited and use optimistic revision checks.
- All holders of APP_PASSWORD share one coordinator workspace. This is not tenant isolation,
  role-based access control, or enterprise authentication. Password entry has a small
  process-local failure throttle; use proper identity management for wider rollout.
- Audit events are local records, not tamper-proof evidence.

Controlled recovery exercise: set TEST_STOP_AFTER_WRITE=1, approve one update, then resume.
The agent deliberately stops before saving the replayed tool output. Disable the flag and
resume the same conversation. Expect REPLAYED_AND_VERIFIED without another update. Keep the
flag off during normal operation.

## 7. Backups and data

```bash
python backup.py backups/food-rescue-backup.db
```

Run on the machine/service owning the disk. Choose a new destination each time. Copy
backups to an access-controlled off-host location. To restore, stop the service, preserve
the current database, replace DATA_DIR/food_rescue_v1.db with a consistent backup, and
restart. Test restoration on a separate copy before depending on it.

This edition uses its own database filename and schema. Do NOT point it at the learning
project's old SQLite file. It does not silently migrate old sessions or records. Re-enter
records or use the explicit demo scenario. Keep your original folder for revision.

## 8. Files to learn next

| File | Responsibility |
|---|---|
| app.py | Browser forms, login, approval buttons, navigation |
| frc/models.py | Validated donation/recipient data, window labels |
| frc/store.py | SQLite, transactions, audit, durable approvals and sessions |
| frc/services.py | Recipient discovery and deterministic assessment |
| frc/integrations.py | Public directory import and optional route estimates |
| frc/agent.py | OpenAI tools, scoped execution, bounded loop, recovery |
| tests/ | Offline service, recovery and UI regression checks |
| render.yaml / Dockerfile | Hosting configuration |

## Sources

- API catalog: https://github.com/public-apis/public-apis
- OpenAI function calling: https://developers.openai.com/api/docs/guides/function-calling
- Streamlit: https://docs.streamlit.io/
- Overpass: https://dev.overpass-api.de/overpass-doc/en/
- OSM food-bank tagging: https://wiki.openstreetmap.org/wiki/Tag:social_facility=food_bank
- OSM attribution/license: https://www.openstreetmap.org/copyright
- openrouteservice directions: https://giscience.github.io/openrouteservice/api-reference/endpoints/directions/
- Render persistent disks: https://render.com/docs/disks

No proposal lifecycle (learning Step 23), multi-agent framework, RAG, MCP or vector database
is required for this release.
