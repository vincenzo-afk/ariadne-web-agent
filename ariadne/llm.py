import os

import httpx


class RateLimited(Exception):
    """Raised when the LLM endpoint returns HTTP 429 or error code RESOURCE_EXHAUSTED."""
    pass


class StubLLM:
    """Deterministic offline stand-in. Never guesses: always abstains."""

    def complete(self, prompt, temperature=0.0, max_tokens=500):
        return '{"answer": null}'


class OpenAICompatLLM:
    """Works with any OpenAI-compatible /chat/completions endpoint:
    Moonshot AI (Kimi), OpenAI, or a local vLLM/Ollama server."""

    def __init__(self, base_url=None, api_key=None, model=None, timeout=60,
                 extra_body=None, fallback_model=None):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.timeout = timeout
        self.extra_body = extra_body
        self.fallback_model = fallback_model

    def complete(self, prompt, temperature=0.0, max_tokens=600, response_format=None):
        payload = {"model": self.model,
                   "messages": [{"role": "user", "content": prompt}],
                   "temperature": temperature, "max_tokens": max_tokens}
        if response_format is not None:
            payload["response_format"] = response_format
        if self.extra_body:
            payload.update(self.extra_body)
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.timeout)
        try:
            body = r.json()
        except Exception:
            body = None
        rate_limited = False
        if r.status_code == 429:
            rate_limited = True
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and err.get("code") == 429:
                rate_limited = True
            if body.get("status") == "RESOURCE_EXHAUSTED":
                rate_limited = True
            if isinstance(err, dict) and err.get("status") == "RESOURCE_EXHAUSTED":
                rate_limited = True
        if rate_limited:
            msg = r.text if (r.text or "").strip() else "rate limited"
            raise RateLimited(msg)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def GeminiLLM(model=None, fallback_model=None, api_key=None, timeout=15):
    """Returns an OpenAICompatLLM configured for Google Gemini's OpenAI-compatible endpoint.
    Reads LLM_API_KEY, LLM_MODEL, LLM_FALLBACK_MODEL from env if args are None."""
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    if api_key is None:
        api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY not set; Gemini unavailable")
    if model is None:
        model = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
    if fallback_model is None:
        fallback_model = os.environ.get("LLM_FALLBACK_MODEL", "gemini-2.5-flash-lite")
    extra_body = {"reasoning_effort": "none"}
    inst = OpenAICompatLLM(base_url=base_url, api_key=api_key, model=model,
                           timeout=timeout, extra_body=extra_body,
                           fallback_model=fallback_model)
    inst.fallback_model = fallback_model
    inst.model = model
    return inst
