# stress_scenarios.py
from datetime import date, datetime
from typing import TYPE_CHECKING
from AlgorithmImports import *

class StressScenarioMixin:

    if TYPE_CHECKING:
        time:       datetime
        live_mode:  bool
        securities: object

        def get_parameter(self, name: str) -> object: ...
        def log(self, msg: str): ...
        def _ParseDateParam(self, value) -> object: ...

    def StressInitialize(self):
        requested = str(self.get_parameter("stress_enabled") or "0").lower() in ("1", "true", "yes", "on")
        # HARD LIVE GUARD: stress-сценарии запрещены в live даже если параметр включён
        self.stress_enabled = bool(requested and not self.live_mode)
        self.stress_name    = str(self.get_parameter("stress_name") or "OIL_HORMUZ").upper()
        self.stress_start   = self._ParseDateParam(self.get_parameter("stress_start")) or date(2022, 2, 1)
        self.stress_days    = int(self.get_parameter("stress_days") or 10)
        if self.live_mode and requested:
            self.log("[STRESS_GUARD] stress_enabled requested in LIVE but forcibly disabled")

    def StressScale(self, sym):
        if self.live_mode:
            return 1.0
        if not getattr(self, "stress_enabled", False):
            return 1.0
        today = self.time.date()
        if today < self.stress_start:
            return 1.0
        d = (today - self.stress_start).days
        if d < 0 or d > self.stress_days:
            return 1.0
        k = min(1.0, max(0.0, d / max(1, self.stress_days)))
        ticker = sym.Value if hasattr(sym, "Value") else str(sym)
        if self.stress_name == "OIL_HORMUZ":
            shocks = {
                "SPY":  1.0 - 0.08 * k,
                "XLE":  1.0 + 0.30 * k,
                "XLB":  1.0 + 0.15 * k,
                "BND":  1.0 - 0.03 * k,
                "GLD":  1.0 + 0.08 * k,
                "GLDM": 1.0 + 0.08 * k,
                "DBC":  1.0 + 0.20 * k,
                "TIP":  1.0 + 0.02 * k,
            }
            return shocks.get(ticker, 1.0)
        return 1.0

    def StressPrice(self, sym, raw_price=None):
        if raw_price is None:
            raw_price = float(self.securities[sym].Price)
        return float(raw_price) * float(self.StressScale(sym))

    def StressCloseArray(self, sym, closes):
        scale = self.StressScale(sym)
        if scale == 1.0:
            return closes
        return closes * scale

    # Short alias used internally in cg_risk_tactical.py for char-budget reasons
    def _sc(self, sym, arr):
        s = self.StressScale(sym)
        return arr if s == 1.0 else arr * s