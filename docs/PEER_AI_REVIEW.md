# Peer AI review configuration

The learning worker always stores a non-empty lesson as a temporary candidate, then attempts bounded public-web research. It also queries up to three configured OpenAI-compatible review endpoints. If no provider is configured or a provider is unavailable, the candidate remains saved and unverified; this never causes a lesson to be discarded.

Set `PEER_AI_PROVIDERS_JSON` in the application's process environment. Do not commit API keys or put their literal values in this JSON. Store each secret in the environment variable named by `api_key_env`.

Example using placeholders only:

```json
[
  {
    "provider": "provider-a",
    "base_url": "https://api.provider-a.example/v1",
    "model": "review-model-name",
    "api_key_env": "PROVIDER_A_API_KEY"
  },
  {
    "provider": "provider-b",
    "base_url": "https://api.provider-b.example/v1",
    "model": "another-review-model",
    "api_key_env": "PROVIDER_B_API_KEY"
  }
]
```

- Endpoints must use HTTPS. HTTP is allowed only for loopback development endpoints.
- Each endpoint must implement the OpenAI-compatible `POST /chat/completions` shape. Provider-specific APIs that are not compatible need a dedicated adapter.
- At most three configured providers are called per candidate. Requests use temperature zero and bounded request/response sizes.
- The prompt asks each reviewer to challenge the lesson, find counterexamples, identify practical uses and limitations, and cite public URLs.
- Responses, provider/model labels, prompt, stance, citations, response hash, and an explicit independence caveat are persisted.
- Provider responses are not trusted instructions. They do not directly promote a candidate or count as proof. Correlated training data and shared errors are possible.
- Web-retrieved pages are stored as contextual evidence until reviewed; retrieval alone does not label them supporting or contradicting.
- If internet access or a provider fails, the failure is best-effort and the temporary candidate remains available.

## Acceptance boundary

Peer-AI agreement is not a truth oracle. A candidate must still pass explicit, reproducible tests before promotion, and a test pass supports only the tested claims and conditions. Non-executable factual claims require reference cases or independently checkable evidence; the system must keep them unverified if no suitable oracle exists.
