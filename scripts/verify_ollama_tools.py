from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


_TOOL_NAME = "autonomous_engineering_capability_probe"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_tool_calling(model: str, *, timeout: int = 45) -> bool:
    name = str(model or "").strip()
    if not name or len(name) > 200:
        raise ValueError("A valid installed Ollama model name is required.")
    payload = {
        "model": name,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": "This is a local compatibility check. Call the provided function exactly once. Do not answer in plain text.",
            },
            {"role": "user", "content": "Call the compatibility probe function now."},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": _TOOL_NAME,
                "description": "Confirms that the model can emit a structured tool call.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }],
        "options": {"temperature": 0, "num_predict": 64},
        "think": False,
    }
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    # Avoid changing the residency of a model that the operator already has loaded.
    try:
        ps_request = urllib.request.Request("http://127.0.0.1:11434/api/ps")
        with opener.open(ps_request, timeout=4) as ps_response:
            running = json.loads(ps_response.read(1_000_001))
        running_models = running.get("models", []) if isinstance(running, dict) else []
        if isinstance(running_models, list) and not any(isinstance(item, dict) and item.get("name") == name for item in running_models):
            payload["keep_alive"] = "0"
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        # Unknown residency is not permission to unload the model.
        pass
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=timeout) as response:
        body = response.read(1_000_001)
    if len(body) > 1_000_000:
        raise ValueError("Ollama tool-probe response exceeded the safety limit.")
    result = json.loads(body.decode("utf-8"))
    message = result.get("message") if isinstance(result, dict) else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    return isinstance(calls, list) and any(
        isinstance(call, dict)
        and isinstance(call.get("function"), dict)
        and call["function"].get("name") == _TOOL_NAME
        for call in calls
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Ollama function/tool calling for a local model.")
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)
    try:
        if not verify_tool_calling(args.model):
            print("The model did not return the required structured tool call.", file=sys.stderr)
            return 1
    except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
        print(f"Local Ollama tool-call probe failed: {exc}", file=sys.stderr)
        return 2
    print(f"Tool calling verified for {args.model}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
