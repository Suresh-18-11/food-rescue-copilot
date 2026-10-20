# Validation and remaining account checks

Validated in the build environment with Python 3.12.14:

- `python -m pytest -q`: 36 tests passed (backend, recovery, input validation,
  approval/decline, idempotency, stale-record conflicts, session conflicts,
  directory/route parsing with mocked provider responses, and Streamlit UI).
- Every screen renders through Streamlit's AppTest.
- Donation browser form writes to an isolated database successfully.
- Password login rejects a wrong password and accepts the configured password.
- Render mode refuses the localhost login bypass.
- Streamlit server starts and `/_stcore/health` returns HTTP 200, `ok`.
- Python compilation succeeds.

Not claimed as validated:

- No live OpenAI call was made with the user's credentials. Model execution in
  automated tests uses deterministic fake responses to avoid charges.
- No live route was requested using a user's openrouteservice key.
- A live public-directory attempt from this environment received an HTTP error;
  live Overpass connectivity/data coverage must be checked on the user's host.
  Parsing, cache behavior and preservation of unknown operational fields are
  covered with provider-shaped test responses. Network failure does not fabricate data.
- Render provisioning/deployment and Docker execution have not been performed.
- This is not an independent food-safety or real-world logistics validation.

After local setup, run the optional demo, one actual model call, an approval and
decline, a directory search in your area, and optional routing with your key.
After deployment, verify login, persistent records after service restart,
conversation resume, pending approval recovery and backup restoration.
