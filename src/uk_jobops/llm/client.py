"""Multi-provider LLM client with free-tier models and graceful fallback."""
from __future__ import annotations

import json
import re
from typing import Any

from ..config import Config


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: Config):
        llm = cfg.settings.get("llm", {})
        self.primary = llm.get("primary", "gemini")
        self.critic = llm.get("critic", "groq")
        self.gemini_model = llm.get("gemini_model", "gemini-2.0-flash")
        self.groq_model = llm.get("groq_model", "llama-3.3-70b-versatile")
        self.s = cfg.secrets
        self.order: list[str] = []
        for p in (self.primary, self.critic):
            if p and p not in self.order:
                self.order.append(p)
        # Last-resort fallback so work continues if the primary's *daily* quota is
        # exhausted. The 4s request pacing prevents per-minute thrash, so Groq is
        # only reached when Gemini is genuinely out, not on transient rate limits.
        for p in ("gemini", "groq"):
            if p not in self.order:
                self.order.append(p)

    def available(self, provider: str) -> bool:
        return bool({"gemini": self.s.gemini_api_key, "groq": self.s.groq_api_key}.get(provider))

    def complete(self, system: str, user: str, *, provider: str | None = None, temperature: float = 0.2) -> str:
        chain = [provider] if provider else []
        chain += [p for p in self.order if p != provider]
        last_err = "no provider available"
        for prov in chain:
            if not prov or not self.available(prov):
                continue
            try:
                if prov == "gemini":
                    return self._gemini(system, user, temperature)
                if prov == "groq":
                    return self._groq(system, user, temperature)
            except Exception as exc:  # try next provider
                last_err = f"{prov}: {exc}"
        raise LLMError(last_err)

    def complete_json(self, system: str, user: str, *, provider: str | None = None) -> dict[str, Any]:
        text = self.complete(system + "\nReturn ONLY valid JSON, no prose, no code fences.", user, provider=provider)
        return _extract_json(text)

    def _gemini(self, system: str, user: str, temperature: float) -> str:
        try:  # current SDK
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.s.gemini_api_key)
            resp = client.models.generate_content(
                model=self.gemini_model, contents=user,
                config=types.GenerateContentConfig(system_instruction=system, temperature=temperature))
            return (resp.text or "").strip()
        except ImportError:  # fallback to legacy SDK
            import google.generativeai as genai

            genai.configure(api_key=self.s.gemini_api_key)
            model = genai.GenerativeModel(self.gemini_model, system_instruction=system,
                                          generation_config={"temperature": temperature})
            return (model.generate_content(user).text or "").strip()

    def _groq(self, system: str, user: str, temperature: float) -> str:
        from groq import Groq

        client = Groq(api_key=self.s.groq_api_key)
        resp = client.chat.completions.create(
            model=self.groq_model, temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return (resp.choices[0].message.content or "").strip()


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("model did not return valid JSON")
