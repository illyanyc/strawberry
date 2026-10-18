"""context-data settings (``TB_*`` / ``TB_CTX_*`` environment variables, section 09).

Every availability delay and schedule is configuration (master prompt: ASSUMED inputs are config,
never code constants); the defaults are documented in docs/business-logic/context-data.md.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from tbcore.context.availability import AvailabilityConfig
from tbcore.service import ServiceSettings


class Settings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="TB_", extra="ignore")

    service: str = "context-data"
    nats_url: str = "nats://nats:4222"
    nats_user: str = "context_data"
    lake_dir: str = "/data/lake"
    broker_gateway_addr: str = "broker-gateway:9090"

    # Universe: instrument IDs from the lake's point-in-time universe; ``ctx_symbols`` overrides.
    ctx_universe: str = "dev"
    ctx_symbols: str = ""
    ctx_history_start: date | None = None

    # Sources (a source without its key or contact stays disabled and is reported as such).
    ctx_fmp_base: str = "https://financialmodelingprep.com"
    ctx_fmp_rate_per_min: int = 50
    ctx_fmp_report_times: bool = False  # set after spike V6 confirms `time` in /stable earnings
    ctx_fred_base: str = "https://api.stlouisfed.org"
    ctx_fred_series: str = "PAYEMS,UNRATE,CPIAUCSL,CPILFESL,PCEPI,GDPC1,ICSA,FEDFUNDS,DGS2,DGS10,T10Y2Y,VIXCLS,VXVCLS"
    ctx_finra_base: str = "https://api.finra.org"
    ctx_sec_base: str = "https://data.sec.gov"
    ctx_sec_tickers_url: str = "https://www.sec.gov/files/company_tickers.json"
    ctx_sec_user_agent: str = ""  # SEC requires "<name> <contact email>"; EDGAR is off until set
    ctx_edgar_forms: str = "8-K,10-Q,10-K,4,SC 13D,SC 13G,S-1,DEF 14A,6-K,20-F"
    ctx_econ_countries: str = "US"

    # News (CX-010..CX-014).
    ctx_news_enabled: bool = True
    ctx_news_store_body: bool = False
    ctx_news_poll_seconds: float = 15.0
    ctx_news_live_window_seconds: int = 300
    ctx_sentiment_scorer: str = "lexicon"  # lexicon | local_llm
    ctx_llm_url: str = ""
    ctx_llm_model: str = ""
    ctx_llm_model_version: str = ""
    ctx_llm_training_cutoff: date | None = None
    ctx_llm_allowed_hosts: str = "localhost,127.0.0.1,host.docker.internal,ollama,llm"
    ctx_llm_timeout_seconds: float = 10.0

    # Schedules (seconds between runs) and freshness limits (CX-019).
    ctx_calendar_every: int = 1800
    ctx_earnings_every: int = 3600
    ctx_fundamentals_every: int = 21600
    ctx_macro_every: int = 3600
    ctx_finra_every: int = 21600
    ctx_edgar_every: int = 900
    ctx_regime_every: int = 3600
    ctx_stale_factor: float = 3.0

    # Availability (CX-004..CX-012); mirrors tbcore.context.availability.AvailabilityConfig.
    ctx_news_delay_s: int = 60
    ctx_sentiment_latency_s: int = 2
    ctx_econ_actual_delay_s: int = 60
    ctx_econ_schedule_lead_days: int = 7
    ctx_earnings_bmo_time: str = Field(default="09:30", pattern=r"^\d\d:\d\d$")
    ctx_earnings_dmh_time: str = Field(default="16:00", pattern=r"^\d\d:\d\d$")
    ctx_earnings_amc_time: str = Field(default="20:00", pattern=r"^\d\d:\d\d$")
    ctx_earnings_unknown_time: str = Field(default="20:00", pattern=r"^\d\d:\d\d$")
    ctx_earnings_schedule_lead_days: int = 14
    ctx_fundamentals_delay_s: int = 300
    ctx_edgar_delay_s: int = 300
    ctx_finra_publish_time: str = Field(default="23:59", pattern=r"^\d\d:\d\d$")
    ctx_finra_fallback_sessions: int = 8
    ctx_macro_default_release_time: str = Field(default="23:59", pattern=r"^\d\d:\d\d$")

    def availability(self) -> AvailabilityConfig:
        return AvailabilityConfig(
            news_delay_s=self.ctx_news_delay_s,
            sentiment_latency_s=self.ctx_sentiment_latency_s,
            econ_actual_delay_s=self.ctx_econ_actual_delay_s,
            econ_schedule_lead_days=self.ctx_econ_schedule_lead_days,
            earnings_bmo_time=self.ctx_earnings_bmo_time,
            earnings_dmh_time=self.ctx_earnings_dmh_time,
            earnings_amc_time=self.ctx_earnings_amc_time,
            earnings_unknown_time=self.ctx_earnings_unknown_time,
            earnings_schedule_lead_days=self.ctx_earnings_schedule_lead_days,
            fundamentals_delay_s=self.ctx_fundamentals_delay_s,
            edgar_delay_s=self.ctx_edgar_delay_s,
            finra_publish_time=self.ctx_finra_publish_time,
            finra_fallback_sessions=self.ctx_finra_fallback_sessions,
            macro_default_release_time=self.ctx_macro_default_release_time,
        )

    def list(self, value: str) -> list[str]:
        return [x.strip() for x in value.split(",") if x.strip()]
