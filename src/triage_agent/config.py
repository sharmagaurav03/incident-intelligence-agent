"""Config loading: yaml file + TRIAGE__ env overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class GateConfig:
    label_min_confidence: float = 0.5
    fix_min_confidence: float = 0.8
    fix_category: str = "code-defect"
    sev1_assist_only: bool = True


@dataclass
class ClusterConfig:
    window_minutes: int = 30
    signature_tokens: list[str] = field(default_factory=lambda: [
        "500", "502", "503", "504", "timeout", "npe", "nullpointer",
        "exception", "failing", "orders", "checkout", "capture", "oom",
        "connection", "refused", "latency",
    ])


@dataclass
class LLMConfig:
    mode: str = "mock"  # mock | real
    api_key_env: str = "ANTHROPIC_API_KEY"
    endpoint: str = "https://api.anthropic.com/v1/messages"
    summary_model: str = "claude-haiku-4-5-20251001"
    diagnosis_model: str = "claude-sonnet-4-6"
    summary_max_tokens: int = 400
    diagnosis_max_tokens: int = 900
    timeout_seconds: int = 60
    max_retries: int = 2

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


@dataclass
class AdapterConfig:
    tickets: str = "file"        # file | servicenow
    logs: str = "file"           # file | gcp_logging
    metrics: str = "file"        # file | gcp_monitoring
    changes: str = "file"
    history: str = "file"
    fix_publisher: str = "file"  # file | github
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    hmac_secret_env: str = "TRIAGE_WEBHOOK_SECRET"
    debounce_seconds: int = 0  # prod: 60-120 so storm siblings arrive first
    idempotency_file: str = ""

    def secret(self) -> str | None:
        return os.environ.get(self.hmac_secret_env)


@dataclass
class AppConfig:
    data_dir: str = "data"
    output_dir: str = "out"
    source_dir: str = "sample_service"
    gates: GateConfig = field(default_factory=GateConfig)
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    adapters: AdapterConfig = field(default_factory=AdapterConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)


def _apply(dc, data):
    for key, value in (data or {}).items():
        if not hasattr(dc, key):
            raise ValueError(f"Unknown config key: {key!r}")
        current = getattr(dc, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply(current, value)
        else:
            setattr(dc, key, value)


def load_config(path: str | Path | None = None) -> AppConfig:
    cfg = AppConfig()
    if path:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        if yaml is None:
            raise RuntimeError("PyYAML not installed but YAML config requested")
        _apply(cfg, yaml.safe_load(p.read_text()) or {})

    # env override convention: TRIAGE__llm__mode=real, TRIAGE__gates__fix_min_confidence=0.9
    # keeps secrets out of yaml and lets the pipeline flip single values
    for env_key, raw in os.environ.items():
        if not env_key.startswith("TRIAGE__"):
            continue
        parts = env_key.split("__")[1:]
        target: Any = cfg
        for part in parts[:-1]:
            target = getattr(target, part)
        leaf = parts[-1]
        old = getattr(target, leaf)
        if isinstance(old, bool):
            setattr(target, leaf, raw.lower() in ("1", "true", "yes"))
        elif isinstance(old, int):
            setattr(target, leaf, int(raw))
        elif isinstance(old, float):
            setattr(target, leaf, float(raw))
        elif isinstance(old, list):
            setattr(target, leaf, [t.strip() for t in raw.split(",") if t.strip()])
        else:
            setattr(target, leaf, raw)
    return cfg
