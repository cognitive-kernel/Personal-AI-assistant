# Knowledge graph source URL policy

## Purpose

A proposed knowledge-graph edge stores a source URL and excerpt before independent review. A proposal is not accepted knowledge and is excluded from graph retrieval until the live source and reviewer requirements are satisfied.

The proposal boundary must reject invalid source authorities before persisting the edge. Deferring malformed URLs to the later review fetch would leave permanently unreviewable proposals in the queue.

## Accepted URL contract

- HTTPS only, with a hostname.
- No username or password component, including empty userinfo components.
- Only an omitted port or standard HTTPS port 443.
- Explicit empty ports, nonnumeric/out-of-range ports, and malformed authority/IPv6 syntax are rejected as `ValueError`.
- Validation occurs before database initialization and edge insertion.

## Review and trust boundary

URL syntax validation does not verify the source or prove the proposed relation. The later graph review still fetches through `web_sources.fetch_public_document`, checks the stored excerpt against the live page, and requires the existing independent reviewer policy. Proposed edges remain excluded from retrieval until accepted.

## Regression coverage

`tests/test_learning_knowledge_graph.py::test_graph_edge_proposal_rejects_invalid_source_authority` covers empty username/password components, explicit empty/non-default/nonnumeric ports, malformed IPv6, and verifies that rejected proposals are not persisted.

No schema migration is required.
