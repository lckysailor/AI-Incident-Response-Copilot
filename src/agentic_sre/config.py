"""Centralized, env-driven configuration for the agentic SRE service."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

RiskLevel = Literal["low", "medium", "high"]

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM Provider
    anthropic_api_key: str = ""
    llm_model: str = "claude-3-5-sonnet-20241022"

    # Datadog API
    dd_api_key: str = ""
    dd_app_key: str = ""
    dd_site: str = "datadoghq.com"

    # OpenTelemetry
    otel_service_name: str = "agentic-sre"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_environment: str = "production"

    # Kubernetes Access
    k8s_in_cluster: bool = False
    kubeconfig: str = "~/.kube/config"
    k8s_namespace: str = "default"

    # Notifications & Approvals
    slack_webhook_url: str = ""
    pagerduty_routing_key: str = ""

    # Guardrails & Circuit Breakers
    auto_remediate_max_risk: RiskLevel = "low"
    max_diagnosis_retries: int = 2
    agent_webhook_secret: str = "change-me"

    def risk_requires_approval(self, risk: RiskLevel) -> bool:
        """Returns True if the risk exceeds the configured auto-remediation ceiling."""
        return _RISK_ORDER[risk] > _RISK_ORDER[self.auto_remediate_max_risk]


@lru_cache
def get_settings() -> Settings:
    return Settings()
