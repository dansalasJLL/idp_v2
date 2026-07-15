"""
IDP Agent — Model Provider Adapters
===================================
One swappable seam between the pipeline and whatever LLM endpoint is sanctioned.

Why this exists
---------------
The hackathon's Claude access is SANDBOX-ONLY — cleared for synthetic contracts, NOT
real client MSAs. Production must run against a JLL-sanctioned endpoint (Falcon) that
is cleared for real data. Everything else in the pipeline (parse, chunk, schema,
reduce, checklist, UI, export) is identical across both. This module is the only place
that changes when the endpoint changes.

    parse -> chunk -> [provider.extract] -> validate -> reduce -> checklist -> UI

Governance rule enforced here
-----------------------------
`cleared_for_real_data` marks whether an endpoint may receive real client text.
`assert_data_allowed()` blocks real-data runs against a sandbox endpoint.

Author: Daniel Salas Castro — JLL Hackathon 2026
"""

from __future__ import annotations

import time
from abc import ABC
from typing import List

DEFAULT_MODEL = "claude-sonnet-4-6"   # fast + cheap + strong structured output
MAX_TOKENS = 4096
MAX_RETRIES = 3

_TOOL_NAME = "record_obligations"


def _wrap_schema(item_schema: dict) -> dict:
    """The extraction tool returns {"obligations": [ <item_schema>, ... ]}."""
    return {
        "type": "object",
        "properties": {"obligations": {"type": "array", "items": item_schema}},
        "required": ["obligations"],
    }


def parse_tool_obligations(content_blocks) -> list:
    """Pull the obligations array out of a tool-use response (find by type, not index).
    Pure function — no SDK dependency — so it's unit-testable on its own."""
    for block in content_blocks:
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else None)
        if btype == "tool_use" and bname == _TOOL_NAME:
            inp = getattr(block, "input", None)
            if inp is None and isinstance(block, dict):
                inp = block.get("input", {})
            return (inp or {}).get("obligations", [])
    return []


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #
class ModelProvider(ABC):
    name: str = "base"
    cleared_for_real_data: bool = False   # may this endpoint receive real client MSAs?
    model: str = "unknown"

    def extract(self, system_prompt: str, user_prompt: str, item_schema: dict) -> List[dict]:
        """Return the raw list of obligation dicts for ONE clause (pre-validation).
        Default delegates to extract_with_response so a provider only needs to
        implement one of the two. Implementations must honor item_schema so the
        rest of the pipeline stays endpoint-agnostic."""
        items, _ = self.extract_with_response(system_prompt, user_prompt, item_schema)
        return items

    def extract_with_response(self, system_prompt: str, user_prompt: str, item_schema: dict):
        """Return (obligations_list, raw_response). The raw response lets the
        telemetry layer read token usage. Default calls extract() and reports no
        usage — so a provider that can't surface usage still works, just without
        token/cost numbers. A provider MUST implement at least one of extract /
        extract_with_response (implementing neither raises)."""
        if type(self).extract is ModelProvider.extract:
            raise NotImplementedError(
                f"{type(self).__name__} must implement extract() or extract_with_response()."
            )
        return self.extract(system_prompt, user_prompt, item_schema), None


# --------------------------------------------------------------------------- #
# Claude via hackathon sponsorship — SANDBOX ONLY
# --------------------------------------------------------------------------- #
class ClaudeProvider(ModelProvider):
    """Anthropic API through the hackathon sponsorship.

    \u26a0 SANDBOX ONLY. Not cleared for real client MSA text — use synthetic contracts.
    For production with real data, swap to FalconProvider (or another sanctioned,
    data-cleared endpoint)."""
    name = "claude-sandbox"
    cleared_for_real_data = False

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = MAX_TOKENS,
                 max_retries: int = MAX_RETRIES):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries

    def extract(self, system_prompt: str, user_prompt: str, item_schema: dict) -> List[dict]:
        items, _ = self.extract_with_response(system_prompt, user_prompt, item_schema)
        return items

    def extract_with_response(self, system_prompt: str, user_prompt: str, item_schema: dict):
        """Like extract(), but also returns the raw response so the caller can read
        token usage for telemetry. Returns (obligations_list, response)."""
        tool = {
            "name": _TOOL_NAME,
            "description": "Record every obligation found in the clause.",
            "input_schema": _wrap_schema(item_schema),
        }
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system_prompt,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": _TOOL_NAME},
                    messages=[{"role": "user", "content": user_prompt}],
                )
                self.last_response = resp
                return parse_tool_obligations(resp.content), resp
            except Exception as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"Claude extraction failed after {self.max_retries} tries: {last_err}")


# --------------------------------------------------------------------------- #
# JLL Falcon — sanctioned, cleared for real client data  (production target)
# --------------------------------------------------------------------------- #
class FalconProvider(ModelProvider):
    """JLL Falcon inference endpoint — the sanctioned production target, cleared for
    real client MSAs (data stays inside JLL's governed envelope).

    This is a real, working HTTP adapter, not a stub — but Falcon's exact request/
    response contract is set by the platform team, so it's configuration-driven:

      endpoint : full URL of the inference endpoint
      api_key  : bearer token (or set env FALCON_API_KEY / pass via header_builder)
      model    : model id Falcon expects
      mode     : "tool"  -> Anthropic-style tool/function-calling payload
                 "json"  -> plain chat payload; response parsed as JSON text
      response_path : dot-path to the obligations array in the JSON response
                      (json mode), e.g. "choices.0.message.content"

    When the platform team confirms the real contract, set these values (ideally
    in one config dict) and nothing else in the pipeline changes. The two modes
    cover the overwhelmingly common cases (OpenAI/Anthropic-compatible tool use,
    or a plain JSON-returning chat endpoint); a bespoke contract only needs
    `_build_payload` / `_parse_response` adjusted here.
    """
    name = "jll-falcon"
    cleared_for_real_data = True

    def __init__(self, endpoint: str = "", api_key: str = "", model: str = "jll-falcon",
                 mode: str = "tool", response_path: str = "", timeout: float = 60.0,
                 max_retries: int = MAX_RETRIES, header_builder=None, **kwargs):
        import os
        self.endpoint = endpoint or os.environ.get("FALCON_ENDPOINT", "")
        self.api_key = api_key or os.environ.get("FALCON_API_KEY", "")
        self.model = model
        self.mode = mode
        self.response_path = response_path
        self.timeout = timeout
        self.max_retries = max_retries
        self.header_builder = header_builder
        self.kwargs = kwargs
        self.last_response = None

    # --- payload / parsing (the only Falcon-contract-specific bits) --------
    def _build_payload(self, system_prompt: str, user_prompt: str, item_schema: dict) -> dict:
        if self.mode == "tool":
            return {
                "model": self.model,
                "max_tokens": MAX_TOKENS,
                "system": system_prompt,
                "tools": [{
                    "name": _TOOL_NAME,
                    "description": "Record every obligation found in the clause.",
                    "input_schema": _wrap_schema(item_schema),
                }],
                "tool_choice": {"type": "tool", "name": _TOOL_NAME},
                "messages": [{"role": "user", "content": user_prompt}],
            }
        # json mode: ask for a bare JSON object, parse the text ourselves
        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system_prompt +
                 "\n\nReturn ONLY a JSON object: {\"obligations\": [ ... ]}. No prose."},
                {"role": "user", "content": user_prompt},
            ],
        }

    def _dig(self, obj, path: str):
        """Follow a dot-path (with numeric indices) into a nested dict/list."""
        cur = obj
        for part in filter(None, path.split(".")):
            if isinstance(cur, list):
                cur = cur[int(part)]
            else:
                cur = cur.get(part)
            if cur is None:
                return None
        return cur

    def _parse_response(self, data: dict) -> List[dict]:
        if self.mode == "tool":
            # Anthropic-style content blocks
            content = data.get("content", data)
            return parse_tool_obligations(content)
        # json mode: text lives at response_path; parse it as JSON
        import json as _json
        text = self._dig(data, self.response_path) if self.response_path else data
        if isinstance(text, str):
            text = _json.loads(text)
        if isinstance(text, dict):
            return text.get("obligations", [])
        return text or []

    def _headers(self) -> dict:
        if self.header_builder:
            return self.header_builder()
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # --- the ModelProvider interface ---------------------------------------
    def extract(self, system_prompt: str, user_prompt: str, item_schema: dict) -> List[dict]:
        items, _ = self.extract_with_response(system_prompt, user_prompt, item_schema)
        return items

    def extract_with_response(self, system_prompt: str, user_prompt: str, item_schema: dict):
        if not self.endpoint:
            raise RuntimeError(
                "FalconProvider needs an endpoint. Set FALCON_ENDPOINT (and "
                "FALCON_API_KEY) or pass endpoint=... — see the class docstring for "
                "the config the platform team must supply."
            )
        import json as _json
        from urllib import request as _request, error as _error

        payload = _json.dumps(self._build_payload(system_prompt, user_prompt, item_schema)).encode("utf-8")
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = _request.Request(self.endpoint, data=payload, headers=self._headers(), method="POST")
                with _request.urlopen(req, timeout=self.timeout) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                self.last_response = data
                return self._parse_response(data), data
            except (_error.URLError, _error.HTTPError, ValueError, KeyError, IndexError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"Falcon extraction failed after {self.max_retries} tries: {last_err}")


# --------------------------------------------------------------------------- #
# JLL GPT — assistant layer (optional; better for interactive Q&A than batch)
# --------------------------------------------------------------------------- #
class JLLGPTProvider(ModelProvider):
    """JLL GPT — the CRE assistant layer on Falcon. Sanctioned and data-cleared, but
    geared to interactive assistance rather than hundreds of batch extraction calls.
    Provided for completeness / a conversational front end. STUB."""
    name = "jll-gpt"
    cleared_for_real_data = True

    def extract(self, system_prompt: str, user_prompt: str, item_schema: dict) -> List[dict]:
        raise NotImplementedError(
            "JLLGPTProvider is a stub. If JLL GPT exposes a programmatic/Skills API, "
            "send the prompt + schema and return the obligations array. For batch "
            "per-clause extraction, FalconProvider is the better fit."
        )


# --------------------------------------------------------------------------- #
# Factory + governance guard
# --------------------------------------------------------------------------- #
_REGISTRY = {
    "claude": ClaudeProvider,
    "falcon": FalconProvider,
    "jllgpt": JLLGPTProvider,
}


def get_provider(name: str = "claude", **kwargs) -> ModelProvider:
    key = name.lower().replace("-", "").replace("_", "").replace(" ", "")
    key = {"claudesandbox": "claude", "jllfalcon": "falcon", "jllgptprovider": "jllgpt"}.get(key, key)
    if key not in _REGISTRY:
        raise ValueError(f"Unknown provider '{name}'. Options: {list(_REGISTRY)}")
    return _REGISTRY[key](**kwargs)


def assert_data_allowed(provider: ModelProvider, contains_real_client_data: bool) -> None:
    """Hard governance gate: refuse to send real client MSA text to a sandbox endpoint."""
    if contains_real_client_data and not provider.cleared_for_real_data:
        raise PermissionError(
            f"Endpoint '{provider.name}' is NOT cleared for real client data. "
            f"Use synthetic contracts here, or switch to a sanctioned endpoint "
            f"(e.g. Falcon) for real MSAs."
        )


# --------------------------------------------------------------------------- #
# Self-test: response parsing + governance guard (no SDK / network needed)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # fake tool-use response shaped like the Anthropic SDK's resp.content
    class Block:
        def __init__(self, **kw): self.__dict__.update(kw)

    blocks = [
        Block(type="text", text="Here are the obligations."),
        Block(type="tool_use", name=_TOOL_NAME, input={"obligations": [
            {"description": "Maintain insurance", "category": "Insurance"},
            {"description": "Pay within 30 days", "category": "Financial"},
        ]}),
    ]
    got = parse_tool_obligations(blocks)
    assert len(got) == 2 and got[0]["category"] == "Insurance", got
    # also works on plain dict blocks
    assert parse_tool_obligations([{"type": "tool_use", "name": _TOOL_NAME,
                                    "input": {"obligations": [{"x": 1}]}}]) == [{"x": 1}]
    # empty when no tool block
    assert parse_tool_obligations([Block(type="text", text="hi")]) == []
    print("parse_tool_obligations: OK")

    # governance guard
    sandbox = ClaudeProvider.__new__(ClaudeProvider)  # don't init SDK
    sandbox.name, sandbox.cleared_for_real_data = "claude-sandbox", False
    assert_data_allowed(sandbox, contains_real_client_data=False)  # fine
    try:
        assert_data_allowed(sandbox, contains_real_client_data=True)
        raise AssertionError("guard should have blocked real data on sandbox")
    except PermissionError:
        print("assert_data_allowed: blocked real data on sandbox endpoint as expected")

    falcon = FalconProvider()
    assert falcon.cleared_for_real_data is True
    assert_data_allowed(falcon, contains_real_client_data=True)  # allowed
    print("Falcon cleared for real data: OK")

    # Falcon payload building + response parsing (no network) --------------
    schema = {"type": "object", "properties": {"description": {"type": "string"}}}
    # tool mode
    ftool = FalconProvider(endpoint="https://example.invalid", mode="tool")
    payload = ftool._build_payload("sys", "user clause", schema)
    assert payload["tools"][0]["name"] == _TOOL_NAME
    assert payload["messages"][0]["content"] == "user clause"
    parsed = ftool._parse_response({"content": [
        {"type": "tool_use", "name": _TOOL_NAME, "input": {"obligations": [{"description": "x"}]}}
    ]})
    assert parsed == [{"description": "x"}], parsed
    # json mode with a nested response_path (OpenAI-like shape)
    fjson = FalconProvider(endpoint="https://example.invalid", mode="json",
                           response_path="choices.0.message.content")
    p2 = fjson._build_payload("sys", "user", schema)
    assert "JSON object" in p2["messages"][0]["content"]
    parsed2 = fjson._parse_response(
        {"choices": [{"message": {"content": '{"obligations": [{"description": "y"}]}'}}]}
    )
    assert parsed2 == [{"description": "y"}], parsed2
    # missing-endpoint guard
    try:
        FalconProvider(endpoint="").extract("s", "u", schema)
        raise AssertionError("should require an endpoint")
    except RuntimeError:
        pass
    print("Falcon payload/parse (tool + json modes) + endpoint guard: OK")

    print("\nAll provider self-tests passed.")
