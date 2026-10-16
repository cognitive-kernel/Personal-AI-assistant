# Experience memory API

## Scope

The local experience-memory API exposes the versioned experience store for manual curation and inspection, supports explicit opt-in capture of source-backed technical research, and queues controlled tests through the Docker sandbox. The public API itself cannot mark records verified; only the controlled runner can promote a record after a real sandbox result and matching evidence.

All endpoints are protected by the same loopback-client dependency as task management. Remote access must not be enabled without a separate authenticated API design.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/experiences/auto-capture/consent` | Read current capture consent and recent audit events |
| POST | `/api/experiences/auto-capture/consent` | Enable capture after explicit `confirm: true` |
| DELETE | `/api/experiences/auto-capture/consent` | Revoke consent for future captures |
| GET | `/api/experiences` | List records; optional `topic`, `status`, and `limit` filters |
| POST | `/api/experiences` | Create an unverified experience record |
| GET | `/api/experiences/search?query=...` | Retrieve verified guidance and separate failed/review-needed warnings |
| GET | `/api/experiences/{id}` | Read a record |
| GET | `/api/experiences/{id}/versions` | Inspect version history |
| PATCH | `/api/experiences/{id}` | Update editable status, source evidence, test plan, or takeaway |
| POST | `/api/experiences/{id}/run-test` | Queue a bounded Python harness that matches the saved test plan; requires sandbox consent and explicit confirmation |

## Trust boundary

- Creation always starts in the domain's default `identified` state.
- Public updates cannot set `status=verified`.
- Public updates reject extra fields, including `test_results` and `_controlled_run`.
- Test results and verified promotion remain reserved for the controlled runner, which records the source hash, pinned image, exit code, duration, and bounded output from an actual sandbox run.
- Test execution is unavailable unless Docker sandbox configuration is valid and the separate sandbox-consent record is enabled.
- The API does not expose rollback or deletion operations.
- HTTPS source evidence and test-plan validation remain enforced by the domain layer.
- Search returns verified records as guidance and failed/review-needed records as warnings; neither is proof that a new environment behaves identically.

## Validation

The route tests cover creation, editing, filtering, version history, rejection of verification/test-result injection, controlled test queuing, missing records, and rejection of non-loopback clients. CI is the validation gate; a local test run was not performed by this change.


## Opt-in research capture

Automatic capture is disabled by default. After explicit local consent, technical research answers with usable HTTPS sources may be saved as `identified` or `needs_review` experiences. The question, answer takeaway, and up to three source URLs with short excerpts are stored locally. Small-talk and research answers without usable sources are not captured. Captured experiences are never marked verified by this path; no test code is executed. Consent grant/revocation events are stored in the settings database, and revocation stops future captures without deleting existing records.


## Source revalidation

- `POST /api/experiences/{id}/refresh-sources` requires an explicit `{"confirm": true}` request and only fetches up to three HTTPS URLs already attached to that experience.
- URL validation, public-destination checks, bounded response size, and safe redirect validation use the existing web-source adapter. No arbitrary URL can be supplied in the request body.
- `GET /api/experiences/{id}/source-checks` returns the durable check history.
- Each check stores a hash of bounded extracted title/text, the prior hash, outcome (`baseline`, `unchanged`, `changed`, `unavailable`, or `blocked`), timestamp, and a bounded error category. Full fetched page content and raw exception details are not persisted.
- A changed fingerprint is not proof of a factual change, and an unchanged fingerprint covers only bounded extracted text. Revalidation never edits or promotes the experience; a person must review changes and run the controlled test separately.
