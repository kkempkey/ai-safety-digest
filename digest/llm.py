"""LLM backend layer.

Two ways to reach Claude, chosen automatically:

1. **API key** (`ANTHROPIC_API_KEY` in .env) — the Anthropic API with structured
   outputs. Guaranteed-valid JSON. Pay-as-you-go.
2. **Claude Code CLI** (default fallback) — headless `claude -p` using the Max
   subscription login already on this Mac. No extra cost; JSON is requested via
   prompt and validated here, with one retry.

Both paths return a parsed dict or raise.
"""

import json
import os
import re
import shutil
import subprocess
from typing import Optional

from . import PROJECT_ROOT

# API model id → claude CLI alias
CLI_MODEL_ALIAS = {
    "claude-haiku-4-5": "haiku",
    "claude-opus-5": "opus",
}

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def backend() -> str:
    return "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"


def _claude_bin() -> str:
    path = shutil.which("claude")
    if path:
        return path
    fallback = os.path.expanduser("~/.local/bin/claude")
    if os.path.exists(fallback):
        return fallback
    raise RuntimeError(
        "No ANTHROPIC_API_KEY set and the `claude` CLI was not found. "
        "Either add an API key to .env or install/log in to Claude Code."
    )


def _call_api(model: str, system: str, user_content: str, schema: dict,
              max_tokens: int) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    kwargs = {}
    if model.startswith("claude-opus"):
        kwargs["thinking"] = {"type": "adaptive"}
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user_content}],
        **kwargs,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused (stop_details=%s)" % response.stop_details)
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _call_cli_once(model: str, system: str, user_content: str,
                   schema: dict) -> dict:
    alias = CLI_MODEL_ALIAS.get(model, model)
    full_system = (
        "%s\n\nYou have NO tools in this session — do not attempt to use any "
        "tool, read any file, or take any action. Produce your answer directly "
        "in a single response.\n\nOUTPUT FORMAT — non-negotiable: respond with "
        "ONLY valid JSON matching this JSON Schema. No prose before or after, "
        "no code fences, no explanations.\nSchema:\n%s"
        % (system, json.dumps(schema))
    )
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    # --max-turns 1 forces a single direct response (no agentic tool loop),
    # which is also what keeps latency sane for opus.
    proc = subprocess.run(
        [_claude_bin(), "-p", "--output-format", "json",
         "--model", alias, "--system-prompt", full_system,
         "--max-turns", "1", "--disallowed-tools", "*"],
        input=user_content, capture_output=True, text=True,
        timeout=900, cwd=str(PROJECT_ROOT), env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError("claude CLI exit %d: %s"
                           % (proc.returncode, (proc.stderr or proc.stdout)[:400]))
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError("claude CLI error: %s" % str(envelope)[:400])
    text = FENCE.sub("", envelope.get("result", "").strip())
    return json.loads(text)


def _validate_top_level(result: dict, schema: dict) -> None:
    for key in schema.get("required", []):
        if key not in result:
            raise ValueError("response missing required key %r" % key)


def structured_call(model: str, system: str, user_content: str, schema: dict,
                    max_tokens: int = 16000, log=print) -> dict:
    """One structured LLM call via whichever backend is available."""
    if backend() == "api":
        return _call_api(model, system, user_content, schema, max_tokens)
    # CLI backend: JSON via prompt; retry once on parse/validation failure
    try:
        result = _call_cli_once(model, system, user_content, schema)
        _validate_top_level(result, schema)
        return result
    except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
        log("  WARN cli call failed (%s); retrying once" % str(exc)[:120])
        result = _call_cli_once(model, system, user_content, schema)
        _validate_top_level(result, schema)
        return result
