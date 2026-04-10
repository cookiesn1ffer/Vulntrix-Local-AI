"""
ModelRouter — directs tasks to the correct Ollama model.

The design separates two concerns:

  reasoning_model  (default: mistral)
      Broad analysis, vulnerability classification, attack-path planning,
      report writing.  Needs to follow instructions and reason over text.

  coding_model     (default: deepseek-coder)
      Exploit generation, script writing, payload crafting.
      Needs strong code completion and structured output.

Either model can be overridden at runtime via config or CLI flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator, Optional

from .ollama_client import OllamaClient, OllamaError


# ─── sensible defaults (users override these in config.py) ───────────────────
DEFAULT_REASONING_MODEL = "mistral"
DEFAULT_CODING_MODEL    = "deepseek-coder"


@dataclass
class ModelConfig:
    reasoning_model : str   = DEFAULT_REASONING_MODEL
    coding_model    : str   = DEFAULT_CODING_MODEL
    temperature_reasoning: float = 0.6
    temperature_coding   : float = 0.3   # lower → more deterministic code
    max_tokens_reasoning : int   = 4096
    max_tokens_coding    : int   = 8192
    stream               : bool  = True   # stream by default for UX


class ModelRouter:
    """
    High-level interface used by the CLI and feature modules.

    Methods map cleanly to pentesting workflows:
      analyse()   → reasoning model
      plan()      → reasoning model
      code()      → coding model
      explain()   → reasoning model
    """

    def __init__(
        self,
        client: Optional[OllamaClient] = None,
        config: Optional[ModelConfig] = None,
    ) -> None:
        self.client = client or OllamaClient()
        self.cfg    = config or ModelConfig()

    # ─── public task methods ─────────────────────────────────────────────────

    def analyse(
        self,
        prompt: str,
        system: Optional[str] = None,
        stream: Optional[bool] = None,
    ) -> str:
        """Run *prompt* through the reasoning model and return the response."""
        return self._call_reasoning(prompt, system=system, stream=stream)

    def plan(
        self,
        prompt: str,
        system: Optional[str] = None,
        stream: Optional[bool] = None,
    ) -> str:
        """Generate a prioritised attack plan via the reasoning model."""
        return self._call_reasoning(prompt, system=system, stream=stream)

    def code(
        self,
        prompt: str,
        system: Optional[str] = None,
        stream: Optional[bool] = None,
    ) -> str:
        """Generate exploit code / scripts via the coding model."""
        return self._call_coding(prompt, system=system, stream=stream)

    def explain(
        self,
        prompt: str,
        system: Optional[str] = None,
        stream: Optional[bool] = None,
    ) -> str:
        """Explain a vulnerability or technique via the reasoning model."""
        return self._call_reasoning(prompt, system=system, stream=stream)

    def stream_analyse(
        self,
        prompt: str,
        system: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """Yield tokens from the reasoning model in real time."""
        yield from self.client.generate_stream(
            self.cfg.reasoning_model, prompt, system=system,
            temperature=self.cfg.temperature_reasoning,
            max_tokens=self.cfg.max_tokens_reasoning,
        )

    def stream_code(
        self,
        prompt: str,
        system: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """Yield tokens from the coding model in real time."""
        yield from self.client.generate_stream(
            self.cfg.coding_model, prompt, system=system,
            temperature=self.cfg.temperature_coding,
            max_tokens=self.cfg.max_tokens_coding,
        )

    # ─── health / introspection ──────────────────────────────────────────────

    def check_models(self) -> dict[str, bool]:
        """
        Return a dict showing whether each configured model is available.
        Handles Ollama's tagging convention (e.g. "mistral" matches "mistral:latest").
        """
        available = set(self.client.list_models())

        def _match(name: str) -> bool:
            return any(
                m == name or m.startswith(name + ":") or name.startswith(m.split(":")[0])
                for m in available
            )

        return {
            self.cfg.reasoning_model: _match(self.cfg.reasoning_model),
            self.cfg.coding_model:    _match(self.cfg.coding_model),
        }

    # ─── private helpers ─────────────────────────────────────────────────────

    def _call_reasoning(
        self,
        prompt: str,
        system: Optional[str],
        stream: Optional[bool],
    ) -> str:
        use_stream = self.cfg.stream if stream is None else stream
        try:
            return self.client.generate(
                model       = self.cfg.reasoning_model,
                prompt      = prompt,
                system      = system,
                temperature = self.cfg.temperature_reasoning,
                max_tokens  = self.cfg.max_tokens_reasoning,
                stream      = use_stream,
            )
        except OllamaError as exc:
            raise OllamaError(
                f"Reasoning model '{self.cfg.reasoning_model}' failed: {exc}"
            ) from exc

    def _call_coding(
        self,
        prompt: str,
        system: Optional[str],
        stream: Optional[bool],
    ) -> str:
        use_stream = self.cfg.stream if stream is None else stream
        try:
            return self.client.generate(
                model       = self.cfg.coding_model,
                prompt      = prompt,
                system      = system,
                temperature = self.cfg.temperature_coding,
                max_tokens  = self.cfg.max_tokens_coding,
                stream      = use_stream,
            )
        except OllamaError as exc:
            raise OllamaError(
                f"Coding model '{self.cfg.coding_model}' failed: {exc}"
            ) from exc
