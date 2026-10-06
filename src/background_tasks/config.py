"""Configuration centralisée des modèles de Jarvis v1.8.

La conversation Native Audio et les tâches d'arrière-plan ont des transports
séparés. Les modèles background utilisent exclusivement ``generateContent``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MAIN_MODEL_ENV = "GEMINI_MODEL"
DEFAULT_MAIN_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
DEFAULT_ROUTER_MODEL = "gemini-3-flash-preview"
DEFAULT_SIMPLE_MODEL = "gemini-3-flash-preview"
DEFAULT_MEDIUM_MODEL = "gemini-3-flash-preview"
DEFAULT_COMPLEX_MODEL = "gemini-3.8-flash"


@dataclass(frozen=True)
class BackgroundModelConfig:
    router_model: str = DEFAULT_ROUTER_MODEL
    simple_model: str = DEFAULT_SIMPLE_MODEL
    medium_model: str = DEFAULT_MEDIUM_MODEL
    complex_model: str = DEFAULT_COMPLEX_MODEL
    max_concurrent_tasks: int = 3
    complex_rpm: int = 5
    complex_rpd: int = 20
    complex_tpm: int = 250_000
    complex_concurrency: int = 1
    timeout_seconds: float = 180.0

    def model_for_complexity(self, complexity) -> str:
        """Mapping unique complexité → modèle, indépendant du Router Gemini."""
        value = getattr(complexity, "value", str(complexity)).lower()
        if value == "complex":
            return self.complex_model
        if value == "medium":
            return self.medium_model
        return self.simple_model

    @classmethod
    def from_env(cls) -> "BackgroundModelConfig":
        def number(name: str, default: int, minimum: int = 1) -> int:
            try:
                return max(minimum, int(os.getenv(name, str(default))))
            except ValueError:
                return default

        try:
            timeout = max(10.0, float(os.getenv("JARVIS_TASK_TIMEOUT", "180")))
        except ValueError:
            timeout = 180.0
        return cls(
            router_model=os.getenv("JARVIS_TASK_ROUTER_MODEL", DEFAULT_ROUTER_MODEL).strip() or DEFAULT_ROUTER_MODEL,
            simple_model=os.getenv("JARVIS_TASK_SIMPLE_MODEL", DEFAULT_SIMPLE_MODEL).strip() or DEFAULT_SIMPLE_MODEL,
            medium_model=os.getenv("JARVIS_TASK_MEDIUM_MODEL", DEFAULT_MEDIUM_MODEL).strip() or DEFAULT_MEDIUM_MODEL,
            complex_model=os.getenv("JARVIS_TASK_COMPLEX_MODEL", DEFAULT_COMPLEX_MODEL).strip() or DEFAULT_COMPLEX_MODEL,
            max_concurrent_tasks=number("JARVIS_TASK_MAX_CONCURRENT", 3),
            complex_rpm=number("JARVIS_TASK_COMPLEX_RPM", 5),
            complex_rpd=number("JARVIS_TASK_COMPLEX_RPD", 20),
            complex_tpm=number("JARVIS_TASK_COMPLEX_TPM", 250_000),
            complex_concurrency=number("JARVIS_TASK_COMPLEX_CONCURRENCY", 1),
            timeout_seconds=timeout,
        )
