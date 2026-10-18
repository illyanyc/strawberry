"""Assistant settings (``TB_*`` environment variables; secrets only from ``/run/secrets``, SE-010)."""

from __future__ import annotations

import json

from pydantic import Field

from tbcore.service import ServiceSettings

ProviderKind = str  # fake | anthropic | openai | ollama | mlx


class Settings(ServiceSettings):
    service: str = "assistant"

    # ---- providers per role (AN-050)
    llm_planner_provider: ProviderKind = "fake"
    llm_coder_provider: ProviderKind = "fake"
    llm_chat_provider: ProviderKind = "fake"
    # The quarantined role is local-only (AN-054): fake, ollama, or mlx.
    llm_quarantine_provider: ProviderKind = "fake"
    # One switch routes every role to llm_local_provider (D26 local-only mode).
    llm_local_only: bool = False
    llm_local_provider: ProviderKind = "ollama"
    llm_fake_mode: str = "heuristic"
    llm_anthropic_model: str = "claude-sonnet-5.5"
    llm_openai_model: str = "gpt-6.1-sol"
    llm_ollama_model: str = "gpt-oss:20b"
    llm_mlx_model: str = "mlx-community/gpt-oss-20b"
    llm_anthropic_url: str = "https://api.anthropic.com"
    llm_openai_url: str = "https://api.openai.com"
    llm_ollama_url: str = "http://host.docker.internal:11434"
    llm_mlx_url: str = "http://host.docker.internal:8090"
    # Allowlisting forward proxy (Compose service llm-egress); empty means direct (tests, native).
    llm_proxy: str = ""
    # host:port entries (AN-053).
    llm_egress_allow: str = (
        "api.anthropic.com:443,api.openai.com:443,host.docker.internal:11434,host.docker.internal:8090"
    )
    # JSON {"provider": [usd_per_mtok_in, usd_per_mtok_out]} overriding the defaults.
    llm_prices: str = ""
    llm_timeout_seconds: float = 120.0
    max_llm_usd_per_day: float = 5.0
    max_llm_usd_per_month: float = 50.0

    # ---- loop thresholds (AN-040..AN-042, AN-004, AN-056)
    plateau_k: int = 3
    plateau_epsilon: float = 0.05
    no_edge_min_trials: int = 20
    no_edge_max_dsr: float = 0.5
    no_edge_min_pbo: float = 0.5
    unreachable_dsr: float = 0.1
    max_failed_rounds: int = 2
    max_active_loops: int = 3
    tool_result_max_bytes: int = 16384

    # ---- default per-lineage budget (AN-020); raising needs step-up in the gateway (AN-061)
    budget_max_trials_per_round: int = 5
    budget_max_cpu_hours_per_day: float = 8.0
    budget_max_concurrent_jobs: int = 2
    budget_max_rounds: int = 10
    budget_max_wall_clock_hours: float = 24.0

    # ---- level 2 promotion limits (AN-062)
    promo_max_promotions: int = 1
    promo_window_days: int = 14
    promo_min_shadow_sessions: int = 10
    promo_cooldown_days: int = 14

    # ---- plumbing
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "platform"
    db_user: str = "svc_assistant"
    nats_url: str = "nats://nats:4222"
    nats_user: str = "assistant"
    grpc_addr: str = "0.0.0.0:9090"
    sandbox_jobs_dir: str = "/jobs"
    sandbox_timeout_seconds: float = 120.0
    chat_retention_days: int = 365
    eval_threshold: float = 0.95
    # Research-service endpoints (sections 10/11). No holdout endpoint exists for the analyst (AN-030).
    ml_addr: str = "ml:9090"
    optimizer_addr: str = "optimizer:9091"
    account_ids: str = Field(default="", description="Comma-separated account identifiers the egress guard blocks")

    def egress_allowlist(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.llm_egress_allow.split(",") if e.strip())

    def prices(self) -> dict[str, tuple[float, float]]:
        if not self.llm_prices.strip():
            return {}
        raw = json.loads(self.llm_prices)
        return {str(k): (float(v[0]), float(v[1])) for k, v in raw.items()}
