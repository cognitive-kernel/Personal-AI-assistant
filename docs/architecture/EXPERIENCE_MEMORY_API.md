# Experience memory API

## Scope

The local experience-memory API exposes the existing versioned experience store for manual curation and inspection. It does not implement automatic experience capture, execute tests, or mark records verified. Those responsibilities remain with a future controlled runner.

All endpoints are protected by the same loopback-client dependency as task management. Remote access must not be enabled without a separate authenticated API design.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/experiences` | List records; optional `topic`, `status`, and `limit` filters |
| POST | `/api/experiences` | Create an unverified experience record |
| GET | `/api/experiences/search?query=...` | Retrieve verified guidance and separate failed/review-needed warnings |
| GET | `/api/experiences/{id}` | Read a record |
| GET | `/api/experiences/{id}/versions` | Inspect version history |
| PATCH | `/api/experiences/{id}` | Update editable status, source evidence, test plan, or takeaway |

## Trust boundary

- Creation always starts in the domain's default `identified` state.
- Public updates cannot set `status=verified`.
- Public updates reject extra fields, including `test_results` and `_controlled_run`.
- Test results and verified promotion remain reserved for a controlled runner that can provide execution evidence.
- The API does not expose rollback or deletion operations.
- HTTPS source evidence and test-plan validation remain enforced by the domain layer.
- Search returns verified records as guidance and failed/review-needed records as warnings; neither is proof that a new environment behaves identically.

## Validation

The route tests cover creation, editing, filtering, version history, rejection of verification/test-result injection, missing records, and rejection of non-loopback clients. CI is the validation gate; a local test run was not performed by this change.
