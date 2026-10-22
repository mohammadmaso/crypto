"""Configuration.

Secrets (Nobitex key pair) live only in the project-root `.env`. Everything else is a
`Settings` document stored in SQLite and editable from the panel.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DATA_DIR = Path(os.environ.get("NOBI_DATA_DIR", ROOT / "var"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "nobimomentum.db"
RESEARCH_DATA = Path(os.environ.get("NOBI_RESEARCH_DATA", Path.home() / "kronos_iran" / "crypto"))

load_dotenv(ROOT / ".env")

MAINNET_URL = "https://apiv2.nobitex.ir"
TESTNET_URL = "https://testnetapiv2.nobitex.ir"
USER_AGENT = "TraderBot/NobiMomentum-0.1"

Mode = Literal["paper", "testnet", "live"]


class Secrets(BaseModel):
    api_key: str = ""
    api_secret: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)


def secrets_for(mode: Mode) -> Secrets:
    if mode == "testnet" and os.environ.get("NOBITEX_TESTNET_API_KEY"):
        return Secrets(api_key=os.environ.get("NOBITEX_TESTNET_API_KEY", ""),
                       api_secret=os.environ.get("NOBITEX_TESTNET_API_SECRET", ""))
    return Secrets(api_key=os.environ.get("NOBITEX_API_KEY", ""),
                   api_secret=os.environ.get("NOBITEX_API_SECRET", ""))


class StrategyParams(BaseModel):
    """STRATEGY.md section 1. Frozen by design; changes only through an approved shadow switch."""
    lookback: int = 14
    combo: bool = False          # rank-average of the 7/14/30-day momentums (PDF section 7); ignores `lookback`
    k: int = 5
    universe_size: int = 30
    min_history: int = 30
    volume_window: int = 30
    gate_sma: int = 100
    gate_on: bool = True
    rebalance_days: int = 3
    tranches: int = 3
    long_frac: float = 0.5
    short_frac: float = 0.5
    min_shorts: int = 3          # fewer shortable names than this -> hold USDT (stay market-neutral)

    @property
    def label(self) -> str:
        return f"{'L7/14/30' if self.combo else f'L{self.lookback}'} k{self.k} {'gated' if self.gate_on else 'always-on'}"


class RiskLimits(BaseModel):
    max_gross: float = 1.0
    max_coin_frac_of_sleeve: float = 0.25
    short_leverage: float = 1.0
    kill_drawdown: float = 0.25          # flatten + pause when equity falls this far from peak
    max_slippage: float = 0.003          # liquidity filter: book walk must fill within this
    stale_data_hours: float = 36.0       # refuse to trade on candles older than this
    min_order_usdt: float = 2.0          # Nobitex minimum is 0.5 USDT; keep a margin
    slippage_alert_mult: float = 2.0
    position_fee_alert_daily: float = 0.001


class Costs(BaseModel):
    """Cost model used by the paper broker, shadow books and backtests."""
    spot_taker_fee: float = 0.0013       # Nobitex USDT markets, level 0 taker
    margin_taker_fee: float = 0.0013
    slippage: float = 0.0005
    position_fee_daily: float = 0.0005   # replaced per market by /margin/markets/list when available


class TelegramSettings(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    proxy_url: str = "http://127.0.0.1:7890"
    daily_report: bool = True
    alerts: bool = True
    shadow_updates: bool = True


class ApprovalSettings(BaseModel):
    enabled: bool = False
    timeout_minutes: int = 120


class ScheduleSettings(BaseModel):
    # Nobitex daily candles close at 00:00 Asia/Tehran (20:30 UTC). Run a few minutes after.
    run_hour_utc: int = 20
    run_minute_utc: int = 35
    catchup_max_hours: float = 12.0
    report_hour_utc: int = 5
    report_minute_utc: int = 0


class ExecutionSettings(BaseModel):
    order_style: Literal["limit_then_market", "market"] = "limit_then_market"
    limit_offset: float = 0.0005
    limit_wait_seconds: int = 20
    limit_retries: int = 3


class ShadowSettings(BaseModel):
    enabled: bool = True
    min_days: int = 90
    sharpe_margin: float = 0.3
    return_margin: float = 0.05


class Settings(BaseModel):
    mode: Mode = "paper"
    paused: bool = False
    capital_usdt: float = 1000.0          # capital the bot may use (paper start equity / live cap)
    nobitex_proxy_url: str = ""           # empty = direct (recommended inside Iran)
    binance_proxy_url: str = "http://127.0.0.1:7890"
    strategy: StrategyParams = Field(default_factory=StrategyParams)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    costs: Costs = Field(default_factory=Costs)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    approval: ApprovalSettings = Field(default_factory=ApprovalSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    shadow: ShadowSettings = Field(default_factory=ShadowSettings)
    live_confirmed: bool = False


def base_url(mode: Mode) -> str:
    return TESTNET_URL if mode == "testnet" else MAINNET_URL
