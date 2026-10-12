# Evidence-backed experience learning

The adaptive loop is now used by the normal chat endpoint for technical, troubleshooting, and explicit research requests; ordinary small talk keeps the direct chat path. It drafts an initial answer, expands the query across languages, searches/indexes approved official documentation when the local index is thin, reuses verified experiences and failure warnings, asks the local model to revise with source context, retries once for missing URL citations or deterministic Python/JSON syntax errors, and records deterministic verification results. Python and JSON code blocks are parsed but never executed. The learning-job path also uses the same persistent experience memory and its own bounded repair checks. Cross-language retrieval expands the original topic through the configured local Ollama model, searches the existing source index with both the original query and variants, and matches prior verified experiences against the same variants. If the local source index is thin, lacks source diversity, or returns weak lexical matches, the job attempts real web discovery through DuckDuckGo HTML, filters results to an explicit allowlist of official technical-documentation hosts, fetches pages through the existing HTTPS/redirect/DNS/size policy, and indexes accepted pages. If query expansion or discovery fails, the learning job falls back without treating the failed attempt as evidence.

## Experience API

- `POST /api/learning/experiences` — record a weakness or failure.
- `GET /api/learning/experiences` — list/filter experiences by topic or status.
- `GET /api/learning/experiences/{id}` — inspect the complete record.
- `PATCH /api/learning/experiences/{id}` — attach source evidence, test plan/results, and update status/takeaway.

## Controlled verification loop

Every accepted learning artifact now runs a bounded deterministic verifier. It checks the language-aware quality gate and target length, requires exactly three non-empty learning outcomes/questions/answer keys (extra items are rejected rather than silently truncated), checks exact source URL attribution, validates saved source excerpts against retrieved text using SHA-256, and parses fenced Python/JSON examples without executing them. Failed quality, contract, citation, or syntax checks trigger one bounded model repair; remaining failures are recorded and rejected or kept as `needs_review`. The run, test names, outcomes, source language, and verification scope are persisted with the experience. A record without a real retrieved source excerpt cannot become `verified`. The public API cannot promote records to `verified` directly.

The verifier does not execute model-generated code or shell commands. Its `verified` status means only that the registered deterministic artifact/provenance checks passed. It does **not** mean arbitrary code was runtime-tested or every factual claim was independently checked against a source. Related `failed`/`needs_review` records are retrieved separately and supplied as cautionary context, never as verified guidance. Unsupported test types remain `needs_review`; the system does not invent a passing test.

## Evidence and status

An experience cannot be marked `verified` unless it has at least one HTTPS source-evidence item, a test plan, and at least one passing test result. This is a structural guard only: the API does not independently verify that the URL supports the claim or that a caller actually executed the declared test. Treat these records as trusted reusable guidance only when the evidence and test results were produced by the application’s controlled workflow.

Source excerpts and URLs are kept in their original language. The generated lesson language is selected from the topic/request; English source text is not translated before being stored or retrieved. A lesson rejected by the final heuristic quality gate is automatically recorded as a `needs_review` experience with the quality-gate result. This captures a failure signal but does not independently identify a factual error.

## Operational boundaries

- Web discovery uses best-effort DuckDuckGo HTML parsing, then permits page ingestion only from the explicit official-documentation host allowlist and through the existing HTTPS, redirect, DNS, content-type, and size controls. Provider markup/network changes can make discovery return no sources; that failure is recorded and does not become evidence.
- Supported runtime-independent checks are deliberately limited to deterministic quality/assessment contracts, relevance-aware source retrieval, source attribution and excerpt integrity, and Python/JSON syntax parsing. Arbitrary generated code and shell commands are never executed. Tasks requiring runtime integration tests, databases, hardware, or external services remain unverified unless a task-specific safe test adapter is available.
- A separate local end-to-end run with the user's configured Ollama model and live network is environment-specific and must be performed after pulling. CI mocks Ollama/network edges and validates the orchestration and deterministic checks; it cannot guarantee the local model's behavior or live search availability.
