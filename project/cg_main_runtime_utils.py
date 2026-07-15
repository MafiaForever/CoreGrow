# region imports
from AlgorithmImports import *
# endregion
# cg_main_runtime_utils.py
# Runtime logging / date helpers extracted from main.py for QC file-size headroom.
from datetime import datetime


class CgMainRuntimeUtilsMixin:
    """Date-parse + filtered log/debug overrides. Behavior matches former main.py."""

    def _ParseDateParam(self, value):
        if not value:
            return None
        try:
            return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
        except Exception:
            return None

    def _LogAllowedAt(self, dt=None) -> bool:
        if not getattr(self, "log_enable", True):
            return False
        try:
            cur = dt.date() if (dt and hasattr(dt, "date")) else self.time.date()
        except Exception:
            return True
        start = getattr(self, "log_start_date", None)
        end = getattr(self, "log_end_date", None)
        if start is not None and cur < start:
            return False
        if end is not None and cur > end:
            return False
        return True

    def log(self, message) -> None:  # type: ignore[override]
        if not self._LogAllowedAt():
            return
        s = str(message)
        if "Runtime Error" not in s and "Traceback" not in s and not s.startswith(("[INIT]", "[EOA]")):
            o = getattr(self, "log_only_prefixes", ())
            if o and not any(s.startswith(p) for p in o):
                return
            m = getattr(self, "log_mute_prefixes", ())
            if m and any(s.startswith(p) for p in m):
                return
        QCAlgorithm.log(self, message)

    def debug(self, message) -> None:  # type: ignore[override]
        if not self._LogAllowedAt():
            return
        s = str(message)
        o = getattr(self, "log_only_prefixes", ())
        if o and not any(s.startswith(p) for p in o):
            return
        m = getattr(self, "log_mute_prefixes", ())
        if m and any(s.startswith(p) for p in m):
            return
        QCAlgorithm.debug(self, message)

    def _EmitWorstDays(self, label="FINAL", top_n=None):
        if not getattr(self, "_daily_returns", None):
            return
        sr = sorted(self._daily_returns, key=lambda x: x[1])
        n5 = max(1, int(len(sr) * 0.05))
        rows = sr[:n5]
        if top_n is not None:
            rows = rows[:top_n]
        sep = "=" * 48
        QCAlgorithm.log(self, f"{sep}")
        QCAlgorithm.log(self, f"WORST_5PCT,{label},{n5}_of_{len(sr)}_days")
        for i, (day, ret) in enumerate(rows, 1):
            QCAlgorithm.log(self, f"W5,{i},{day},{ret*100:+.2f}%")
        QCAlgorithm.log(self, f"{sep}")


def AttachCgMixins(target_cls, mixins):
    # LEAN/Python.NET: never setattr managed read-only members (e.g. log/debug).
    # Those overrides must be declared on the algorithm class body instead.
    import inspect
    _skip = frozenset(("log", "debug", "Log", "Debug"))
    for _cls in mixins:
        for _name, _fn in inspect.getmembers(_cls, predicate=inspect.isfunction):
            if _name in _skip:
                continue
            try:
                setattr(target_cls, _name, _fn)
            except (AttributeError, TypeError):
                continue
