"""Multi-provider LLM client with per-task routing and graceful fallback.

Scoring and tailoring can run on different providers (e.g. DeepSeek for high-volume
fit-scoring, Gemini Pro for quality CV tailoring) so their quotas never collide.
If a task's chosen provider has no key or errors, it falls back through the others."""
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
        self.s = cfg.secrets
        self.critic = llm.get("critic", "")   # optional second-model critique in tailoring
        self.models = {
            "gemini": llm.get("gemini_model", "gemini-2.0-flash"),
            "groq": llm.get("groq_model", "llama-3.3-70b-versatile"),
            "deepseek": llm.get("deepseek_model", "deepseek-chat"),
        }
        primary = llm.get("primary", "gemini")
        # per-task routing (parallel model usage)
        self.score_provider = llm.get("score_provider", primary)
        self.score_model = llm.get("score_model") or self.models.get(self.score_provider)
        self.tailor_provider = llm.get("tailor_provider", primary)
        self.tailor_model = llm.get("tailor_model") or self.models.get(self.tailor_provider)
        # global fallback order: task providers first, then the rest
        self.order: list[str] = []
        for p in (self.score_provider, self.tailor_provider, primary, self.critic,
                  "gemini", "deepseek", "groq"):
            if p and p not in self.order:
                self.order.append(p)

    def available(self, provider: str) -> bool:
        keys = {"gemini": self.s.gemini_api_key, "groq": self.s.groq_api_key,
                "deepseek": self.s.deepseek_api_key}
        return bool(keys.get(provider))

    def complete(self, system: str, user: str, *, provider: str | None = None,
                 model: str | None = None, temperature: float = 0.2) -> str:
        chain = ([provider] if provider else []) + [p for p in self.order if p != provider]
        last_err = "no provider available (check API keys)"
        for prov in chain:
            if not prov or not self.available(prov):
                continue
            m = model if (prov == provider and model) else self.models.get(prov)
            try:
                if prov == "gemini":
                    return self._gemini(system, user, temperature, m)
                if prov == "groq":
                    return self._groq(system, user, temperature, m)
                if prov == "deepseek":
                    return self._deepseek(system, user, temperature, m)
            except Exception as exc:  # try next provider
                last_err = f"{prov}: {exc}"
        raise LLMError(last_err)

    def complete_json(self, system: str, user: str, *, provider: str | None = None,
                      model: str | None = None) -> dict[str, Any]:
        text = self.complete(system + "\nReturn ONLY valid JSON, no prose, no code fences.",
                             user, provider=provider, model=model)
        return _extract_json(text)

    def _gemini(self, system: str, user: str, temperature: float, model: str | None = None) -> str:
        model = model or self.models["gemini"]
        try:  # current SDK
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.s.gemini_api_key)
            resp = client.models.generate_content(
                model=model, contents=user,
                config=types.GenerateContentConfig(system_instruction=system, temperature=temperature))
            return (resp.text or "").strip()
        except ImportError:  # legacy SDK
            import google.generativeai as genai

            genai.configure(api_key=self.s.gemini_api_key)
            gm = genai.GenerativeModel(model, system_instruction=system,
                                       generation_config={"temperature": temperature})
            return (gm.generate_content(user).text or "").strip()

    def _groq(self, system: str, user: str, temperature: float, model: str | None = None) -> str:
        from groq import Groq

        client = Groq(api_key=self.s.groq_api_key)
        resp = client.chat.completions.create(
            model=model or self.models["groq"], temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return (resp.choices[0].message.content or "").strip()

    def _deepseek(self, system: str, user: str, temperature: float, model: str | None = None) -> str:
        import requests  # OpenAI-compatible REST API

        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {self.s.deepseek_api_key}",
                     "Content-Type": "application/json"},
            json={"model": model or self.models["deepseek"], "temperature": temperature,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=120)
        if r.status_code != 200:
            raise LLMError(f"deepseek {r.status_code}: {r.text[:200]}")
        return (r.json()["choices"][0]["message"]["content"] or "").strip()


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("model did not return valid JSON")
