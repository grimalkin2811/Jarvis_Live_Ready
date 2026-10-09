"""Configuration centralisée des modèles et limites background v1.8."""
from __future__ import annotations

import os
from dataclasses import dataclass

MAIN_MODEL_ENV = "GEMINI_MODEL"
DEFAULT_MAIN_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
DEFAULT_COMPLEX_MODEL = "gemini-3.8-flash"


@dataclass(frozen=True)
class BackgroundModelConfig:
    # Un hint est seulement prioritaire dans la liste API; il doit quand même
    # annoncer BidiGenerateContent et réussir un vrai tour Live transcrit.
    live_model_hint: str = ""
    complex_model: str = DEFAULT_COMPLEX_MODEL
    max_concurrent_tasks: int = 3
    complex_rpm: int = 5
    complex_rpd: int = 20
    complex_tpm: int = 250_000
    complex_concurrency: int = 1
    timeout_seconds: float = 90.0
    task_timeout_seconds: float = 240.0
    retry_attempts: int = 3
    retry_base_delay: float = 1.0

    def model_for_complexity(self, complexity, *, live_model: str | None = None) -> str:
        value = getattr(complexity, "value", str(complexity)).lower()
        if value == "complex":
            return self.complex_model
        if not live_model:
            raise RuntimeError("Le modèle Gemini 3 Live n'a pas encore été découvert et validé.")
        return live_model

    @classmethod
    def from_env(cls) -> "BackgroundModelConfig":
        def number(name: str, default: int, minimum: int = 1) -> int:
            try:
                return max(minimum, int(os.getenv(name, str(default))))
            except ValueError:
                return default

        def decimal(name: str, default: float, minimum: float) -> float:
            try:
                return max(minimum, float(os.getenv(name, str(default))))
            except ValueError:
                return default

        return cls(
            live_model_hint=os.getenv("JARVIS_TASK_LIVE_MODEL", "").strip(),
            complex_model=os.getenv("JARVIS_TASK_COMPLEX_MODEL", DEFAULT_COMPLEX_MODEL).strip() or DEFAULT_COMPLEX_MODEL,
            max_concurrent_tasks=number("JARVIS_TASK_MAX_CONCURRENT", 3),
            complex_rpm=number("JARVIS_TASK_COMPLEX_RPM", 5),
            complex_rpd=number("JARVIS_TASK_COMPLEX_RPD", 20),
            complex_tpm=number("JARVIS_TASK_COMPLEX_TPM", 250_000),
            complex_concurrency=number("JARVIS_TASK_COMPLEX_CONCURRENCY", 1),
            timeout_seconds=decimal("JARVIS_TASK_TIMEOUT", 90.0, 10.0),
            task_timeout_seconds=decimal("JARVIS_TASK_TOTAL_TIMEOUT", 240.0, 30.0),
            retry_attempts=number("JARVIS_TASK_RETRY_ATTEMPTS", 3),
            retry_base_delay=decimal("JARVIS_TASK_RETRY_BASE_DELAY", 1.0, 0.1),
        )
