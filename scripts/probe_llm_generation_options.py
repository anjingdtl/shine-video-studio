"""Probe which generation options the real text provider honours.

The acceptance run drives a hosted reasoning model.  That model documents a
thinking depth that defaults to its longest setting, and a completion budget
that counts the thinking tokens.  A crew call that thinks for minutes can
outlive the upstream connection window, so the acceptance configuration has to
know which options the provider actually accepts before it relies on them.

The provider key is loaded from a DPAPI secret store into this process and is
never printed, logged or written to the evidence: only the HTTP status, the
finish reason and the token usage are recorded.

    python scripts/probe_llm_generation_options.py \
        --source-secret-dir .local-acceptance/closeout-20261025/secrets \
        --source-ref dpapi:ffbf39c1-1a30-459b-8e4e-cca7ba10b3cd.secret
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

PROMPT = "Answer with one JSON object and nothing else: {\"ok\": true}"


def probe(client: httpx.Client, base_url: str, model: str, key: str, options: dict) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0.1,
        **options,
    }
    started = time.perf_counter()
    try:
        response = client.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
    except httpx.HTTPError as exc:
        return {"options": options, "transport_error": type(exc).__name__}
    latency_ms = int((time.perf_counter() - started) * 1000)
    result: dict = {"options": options, "http_status": response.status_code, "latency_ms": latency_ms}
    try:
        body = response.json()
    except ValueError:
        result["body_shape"] = "not-json"
        return result
    if response.status_code >= 400:
        # The provider's own error shape, never the key and never a payload
        # that could contain one.
        error = body.get("error") if isinstance(body, dict) else None
        result["error_code"] = (error or {}).get("code") if isinstance(error, dict) else None
        result["error_type"] = (error or {}).get("type") if isinstance(error, dict) else None
        result["base_resp_status"] = (body.get("base_resp") or {}).get("status_code") if isinstance(body, dict) else None
        return result
    choices = body.get("choices") or []
    result["finish_reason"] = choices[0].get("finish_reason") if choices else None
    result["usage"] = body.get("usage") or {}
    message = choices[0].get("message", {}) if choices else {}
    result["has_content"] = bool((message.get("content") or "").strip())
    result["has_reasoning"] = bool((message.get("reasoning_content") or "").strip())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the provider's generation options")
    parser.add_argument("--source-secret-dir", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--base-url", default="https://api.minimax.cn/v1")
    parser.add_argument("--model", default="MiniMax-M3.1-Flash")
    parser.add_argument("--evidence", default="")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "backend"))
    from shine.llm.secrets import SecretStore

    store = SecretStore(Path(args.source_secret_dir).resolve())
    api_key = store.load(args.source_ref)
    if not api_key:
        print("The source secret could not be loaded", file=sys.stderr)
        return 2

    candidates = [
        {},
        {"max_completion_tokens": 12288},
        {"reasoning_effort": "medium"},
        {"max_completion_tokens": 12288, "reasoning_effort": "medium"},
    ]
    results = []
    with httpx.Client(timeout=180) as client:
        for options in candidates:
            results.append(probe(client, args.base_url, args.model, api_key, options))

    evidence = {
        "base_url": args.base_url,
        "model": args.model,
        "probes": results,
    }
    if args.evidence:
        Path(args.evidence).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
