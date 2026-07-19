# cg_damage_duration_d03b_export.py -- D0.3B1 bounded CSV/JSON export helpers.
# Diagnostic research only. No object-store writes, no orders.
from __future__ import annotations
import csv
import io
import json
from collections import deque

from cg_damage_duration_d03b_accounting import (
    SCHEMA_VERSION, MAX_CHECKPOINT_ROWS, MAX_EPISODE_ROWS, UNAVAILABLE,
)

ORDINARY_LOG_LIMIT = 100 * 1024


def _cell(v):
    if v is None:
        return UNAVAILABLE
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


class PolicyRuntimeExporter:
    """Bounded in-memory export buffers for checkpoint and episode rows."""

    def __init__(self, max_ck=MAX_CHECKPOINT_ROWS, max_ep=MAX_EPISODE_ROWS):
        self.checkpoint_rows = deque(maxlen=int(max_ck))
        self.episode_rows = deque(maxlen=int(max_ep))
        self.counters = {
            "checkpoint_rows_written": 0,
            "episode_rows_written": 0,
            "export_bytes": 0,
            "truncated": 0,
        }

    def add_checkpoint_rows(self, rows):
        for r in rows or []:
            self.checkpoint_rows.append(dict(r))
            self.counters["checkpoint_rows_written"] += 1

    def add_episode_row(self, row):
        if row is None:
            return
        self.episode_rows.append(dict(row))
        self.counters["episode_rows_written"] += 1

    def to_csv(self, rows, fieldnames=None):
        rows = list(rows or [])
        if not rows:
            return ""
        fn = fieldnames or list(rows[0].keys())
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fn, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k, UNAVAILABLE)) for k in fn})
        text = buf.getvalue()
        self.counters["export_bytes"] = max(self.counters["export_bytes"], len(text.encode("utf-8")))
        if len(text.encode("utf-8")) > ORDINARY_LOG_LIMIT:
            self.counters["truncated"] += 1
            # compact: header + last N lines that fit
            lines = text.splitlines()
            out = [lines[0]]
            size = len(lines[0]) + 1
            for ln in reversed(lines[1:]):
                add = len(ln) + 1
                if size + add > ORDINARY_LOG_LIMIT:
                    break
                out.insert(1, ln)
                size += add
            text = "\n".join(out) + "\n"
        return text

    def checkpoint_csv(self):
        return self.to_csv(self.checkpoint_rows)

    def episode_csv(self):
        return self.to_csv(self.episode_rows)

    def schema_json(self, schema_obj):
        text = json.dumps(schema_obj, indent=2, default=_cell)
        self.counters["export_bytes"] = max(
            self.counters["export_bytes"], len(text.encode("utf-8")))
        return text

    def summary(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "checkpoint_rows": len(self.checkpoint_rows),
            "episode_rows": len(self.episode_rows),
            "counters": dict(self.counters),
            "ordinary_log_limit": ORDINARY_LOG_LIMIT,
            "below_ordinary_log_limit": self.counters["export_bytes"] <= ORDINARY_LOG_LIMIT
            or self.counters["truncated"] > 0,
        }
