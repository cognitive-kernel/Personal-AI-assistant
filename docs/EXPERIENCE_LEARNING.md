# Experience learning loop: current implementation

The API now persists a learning experience independently from generated lesson text. Each record stores the observed problem, a hypothesis, source evidence, a test plan, actual test results, a takeaway, a status, and a version.

## API

- `POST /api/learning/experiences` — record a weakness or failure.
- `GET /api/learning/experiences` — list/filter experiences by topic or status.
- `GET /api/learning/experiences/{id}` — inspect the complete record.
- `PATCH /api/learning/experiences/{id}` — attach source evidence, test plan/results, and update status/takeaway.

## Verification invariant

An experience cannot be marked `verified` unless it has at least one HTTPS source-evidence item, a test plan, and at least one passing test result. The API validates the record shape and preserves it in SQLite. It does not claim that a URL itself proves a claim or that a supplied test result was independently executed.

## Not yet implemented

This increment is the persistent evidence-and-test record, not the complete autonomous human-like learning loop. Automatic general-web search, model-generated test execution in a sandbox, automatic retry after test failure, regression-suite execution, and automatic promotion of verified experiences into retrieval are still required. Do not describe the whole loop as complete until those behaviors have integration tests against a controlled test environment.
