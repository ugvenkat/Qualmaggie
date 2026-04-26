"""
Application settings for QualMaggie.

Loading priority (highest wins):
  1. Environment variables
  2. .env file  (DB_CONNECTION_STRING and other secrets)
  3. settings.json  (trading parameters — PascalCase keys)
  4. Field defaults below

Usage:
    from backend.config.settings import get_settings
    s = get_settings()
    print(s.portfolio_size)
    print(s.data_folder)          # returns pathlib.Path, never a raw string
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, JsonConfigSettingsSource, SettingsConfigDict
from pydantic_settings import PydanticBaseSettingsSource

# Resolved at import time — works correctly regardless of cwd or how the
# process is launched on Windows.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        json_file=str(PROJECT_ROOT / "settings.json"),
        env_file=str(PROJECT_ROOT / ".env"),
        populate_by_name=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority (highest first): env vars → .env file → settings.json → field defaults
        return (
            env_settings,
            dotenv_settings,
            JsonConfigSettingsSource(settings_cls),
            init_settings,
        )

    # ------------------------------------------------------------------
    # Database  —  loaded from settings.json key "DatabaseUrl".
    # Uses Windows Authentication (trusted_connection=yes); no username
    # or password required.  Override via DB_CONNECTION_STRING env var
    # or .env file if needed.
    # ------------------------------------------------------------------
    db_connection_string: str = Field(
        default=(
            "mssql+pyodbc://localhost/QualMaggie"
            "?driver=ODBC+Driver+17+for+SQL+Server"
            "&trusted_connection=yes"
        ),
        alias="DatabaseUrl",
        validation_alias="DatabaseUrl",
    )

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------
    portfolio_size: float = Field(100_000.0, alias="PortfolioSize")
    risk_per_trade: float = Field(1.5, alias="RiskPerTrade")            # %
    max_open_positions: int = Field(5, alias="MaxOpenPositions")
    max_capital_deployed_pct: float = Field(50.0, alias="MaxCapitalDeployedPct")  # %

    # ------------------------------------------------------------------
    # Trade management
    # ------------------------------------------------------------------
    partial_sell_days: int = Field(4, alias="PartialSellDays")          # sell 50% at day N
    stop_loss_adr_multiplier: float = Field(1.5, alias="StopLossADRMultiplier")
    min_stop_pct: float = Field(0.0, alias="MinStopPct")                  # % floor: stop never closer than this % from entry
    max_hold_days: int = Field(20, alias="MaxHoldDays")
    max_hold_extended_days: int = Field(60, alias="MaxHoldExtendedDays")  # extended hold if gain >= max_hold_extend_pct
    max_hold_bypass_pct: float = Field(10.0, alias="MaxHoldBypassPct")   # % gain → no MaxHold, only trail/stop exits
    max_hold_extend_pct: float = Field(5.0, alias="MaxHoldExtendPct")    # % gain → extend to MaxHoldExtendedDays
    trail_activation_pct: float = Field(3.0, alias="TrailActivationPct")         # % gain to activate EMA trail
    partial_sell_min_profit_pct: float = Field(3.0, alias="PartialSellMinProfitPct")  # % gain to allow partial sell
    trail_ema_period: int = Field(10, alias="TrailEMAPeriod")                    # EMA period for trailing stop
    wide_trail_activation_pct: float = Field(8.0, alias="WideTrailActivationPct")   # % gain to switch to wide EMA trail
    wide_trail_ema_period: int = Field(20, alias="WideTrailEMAPeriod")              # EMA period for wide trailing stop

    # ------------------------------------------------------------------
    # Stock universe filters
    # ------------------------------------------------------------------
    min_stock_price: float = Field(20.0, alias="MinStockPrice")
    min_avg_volume: int = Field(2_000_000, alias="MinAvgVolume")
    min_atr_pct: float = Field(2.0, alias="MinATRPct")
    max_atr_pct: float = Field(20.0, alias="MaxATRPct")
    min_institutional_ownership_pct: float = Field(30.0, alias="MinInstitutionalOwnershipPct")

    # ------------------------------------------------------------------
    # Position limits
    # ------------------------------------------------------------------
    max_positions_per_sector: int = Field(2, alias="MaxPositionsPerSector")

    # ------------------------------------------------------------------
    # Warnings & blacklist
    # ------------------------------------------------------------------
    earnings_hard_block: bool = Field(False, alias="EarningsHardBlock")
    earnings_warning_days: int = Field(10, alias="EarningsWarningDays")
    macro_event_warning_days: int = Field(5, alias="MacroEventWarningDays")
    blacklist_days: int = Field(10, alias="BlacklistDays")

    # ------------------------------------------------------------------
    # Market filter & relative strength
    # ------------------------------------------------------------------
    market_filter_ticker: str = Field("SPY", alias="MarketFilterTicker")
    rs_lookback_days: int = Field(126, alias="RSLookbackDays")           # ~6 months
    breakout_volume_factor: float = Field(1.0, alias="BreakoutVolumeFactor")

    # ------------------------------------------------------------------
    # VCP pattern parameters
    # ------------------------------------------------------------------
    vcp_min_contractions: int = Field(2, alias="VCPMinContractions")
    vcp_preferred_contractions: int = Field(3, alias="VCPPreferredContractions")
    vcp_tightness_factor_pct: float = Field(25.0, alias="VCPTightnessFactorPct")
    vcp_max_depth_pct: float = Field(35.0, alias="VCPMaxDepthPct")
    vcp_min_days_in_base: int = Field(10, alias="VCPMinDaysInBase")
    vcp_max_days_in_base: int = Field(60, alias="VCPMaxDaysInBase")

    # ------------------------------------------------------------------
    # VCP entry quality filters (Fixes 2, 3, 4)
    # ------------------------------------------------------------------
    min_prior_move_pct: float = Field(30.0, alias="MinPriorMovePct")          # % prior run-up before base
    prior_move_lookback_days: int = Field(126, alias="PriorMoveLookbackDays") # days to look back for prior move
    max_stop_as_adr_fraction: float = Field(0.67, alias="MaxStopAsADRFraction") # reject if stop_pct > ADR_pct × this
    min_setup_quality_score: int = Field(6, alias="MinSetupQualityScore")     # 0–10; reject below threshold

    # ------------------------------------------------------------------
    # Data folders — typed as Path so all consumers use pathlib.
    # Defaults are PROJECT_ROOT-relative; settings.json values (strings)
    # are auto-coerced to Path by pydantic v2 then normalised below.
    # ------------------------------------------------------------------
    data_input_folder: Path = Field(PROJECT_ROOT / "dataInput", alias="DataInputFolder")
    data_folder: Path = Field(PROJECT_ROOT / "data", alias="DataFolder")
    export_folder: Path = Field(PROJECT_ROOT / "data" / "exports", alias="ExportFolder")
    cache_folder: Path = Field(PROJECT_ROOT / "data" / "cache", alias="CacheFolder")

    @field_validator("data_input_folder", "data_folder", "export_folder", "cache_folder", mode="before")
    @classmethod
    def normalise_path(cls, v: object) -> Path:
        """
        Accept str or Path from any source (JSON, env var, default).
        Strips trailing separators so Path comparisons are consistent on Windows.
        """
        return Path(str(v).rstrip("/\\"))

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def risk_per_trade_decimal(self) -> float:
        """RiskPerTrade as a fraction (e.g. 1.5 % → 0.015)."""
        return self.risk_per_trade / 100.0

    @property
    def max_capital_deployed_decimal(self) -> float:
        """MaxCapitalDeployedPct as a fraction."""
        return self.max_capital_deployed_pct / 100.0

    @property
    def max_capital_deployed(self) -> float:
        """Maximum dollar amount that may be deployed simultaneously."""
        return self.portfolio_size * self.max_capital_deployed_decimal

    @property
    def risk_dollars_per_trade(self) -> float:
        """Dollar risk per trade based on current portfolio size."""
        return self.portfolio_size * self.risk_per_trade_decimal

    # ------------------------------------------------------------------
    # Startup helper
    # ------------------------------------------------------------------
    def ensure_folders_exist(self) -> None:
        """Create data directories if they don't exist. Call once at app startup."""
        for folder in (self.data_input_folder, self.data_folder, self.export_folder, self.cache_folder):
            folder.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton for the process lifetime)."""
    return Settings()
