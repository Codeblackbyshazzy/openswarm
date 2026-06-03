"""
Granular, persisted metrics for the browser sub-agent.

Records one JSONL line per tool call and one summary line per task so we can
answer, after the fact: did the task complete, how long did each tool take,
how many tokens (cost) it burned, which tier did the work, and what errors
recurred. Pure best-effort: every call is wrapped so a metrics failure can
never break the agent loop.

Files (under DATA_ROOT/browser_metrics/, env-overridable):
  events.jsonl        one line per tool call
  tasks.jsonl         one line per finished task (with a recurring-error rollup)
  skill_events.jsonl  one line per skill-lifecycle transition (learn / promote /
                      edit / quarantine / demote / compose / invalidate), so we
                      can tell whether the skill layer ACTUALLY speeds repeats up
                      or is silently thrashing (re-learning every run, never
                      promoting), which is the ghost that "completes" but never
                      delivers the win.
"""

import json
import logging
import os
import time
from collections import Counter

logger = logging.getLogger(__name__)

# Map each tool to the waterfall tier it represents, so per-tier speed/cost
# rolls up cleanly. Control/meta tools are their own bucket.
_TIER = {
    "BrowserDetectWebMCP": "t1_webmcp",
    "BrowserListRoutes": "t2_route_list",
    "BrowserReplayRoute": "t2_route_replay",
    "BrowserListInteractives": "t3_action_surface",
    "BrowserClickIndex": "t3_action_surface",
    "BrowserGetText": "t4_content",
    "BrowserGetElements": "t4_content",
    "BrowserScreenshot": "t5_vision",
    "BrowserNavigate": "nav",
    "BrowserClick": "ui_click",
    "BrowserType": "ui_type",
    "BrowserPressKey": "ui_key",
    "BrowserScroll": "ui_scroll",
    "BrowserBatch": "ui_batch",
    "BrowserEvaluate": "ui_eval",
    "BrowserWait": "wait",
    "ReportProgress": "meta",
    "RequestHumanIntervention": "meta_hitl",
}


def tier_for(tool_name: str) -> str:
    return _TIER.get(tool_name, "other")


def _metrics_dir() -> str:
    override = os.environ.get("OPENSWARM_BROWSER_METRICS_DIR")
    if override:
        base = override
    else:
        try:
            from backend.config.paths import DATA_ROOT
            base = os.path.join(DATA_ROOT, "browser_metrics")
        except Exception:
            import tempfile
            base = os.path.join(tempfile.gettempdir(), "openswarm_browser_metrics")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return base


def _append(filename: str, obj: dict) -> None:
    try:
        path = os.path.join(_metrics_dir(), filename)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, default=str) + "\n")
    except Exception as e:
        logger.debug(f"[browser-metrics] write failed: {e}")


def record_tool(session_id, browser_id, turn, tool, elapsed_ms, ok, error,
                is_loop, stagnation_streak, result_len) -> None:
    """One line per executed tool call. Best-effort."""
    _append("events.jsonl", {
        "ts": time.time(),
        "session_id": session_id,
        "browser_id": browser_id,
        "turn": turn,
        "tool": tool,
        "tier": tier_for(tool),
        "elapsed_ms": elapsed_ms,
        "ok": bool(ok),
        "error": (error or "")[:160] if not ok else "",
        "is_loop": bool(is_loop),
        "stagnation_streak": stagnation_streak,
        "result_len": result_len,
    })
    # Human-greppable one-liner too, so it shows in the [backend] terminal pane.
    status = "OK" if ok else "ERR"
    logger.info(
        f"[browser-metrics] {tool} tier={tier_for(tool)} {elapsed_ms}ms {status} "
        f"turn={turn}{' LOOP' if is_loop else ''}"
        f"{f' STAGN={stagnation_streak}' if stagnation_streak else ''}"
    )


def record_skill_event(kind, host, task_sig, rev=0, state="", extra=None) -> None:
    """One line per skill-lifecycle transition. Best-effort. `kind` is one of
    learn / edit / promote / quarantine / demote / compose / invalidate. This is
    what lets the analyzer prove the skill layer is helping (promotes accumulate,
    repeats replay) vs. silently thrashing (re-learn loops, never promotes)."""
    _append("skill_events.jsonl", {
        "ts": time.time(), "kind": kind, "host": host, "task_sig": task_sig,
        "rev": rev, "state": state, "extra": extra or {},
    })


def record_task(session_id, browser_id, task, status, started_at, turns,
                action_log, tokens, path="llm", task_sig="") -> dict:
    """One summary line per finished task: completion, total time, per-tier
    latency, token cost, and the recurring-error rollup. `path` records HOW the
    task finished (replay = no-LLM fast path, llm = full agent, llm_fallback =
    full agent after a replay miss) so we can measure the replay speedup and spot
    repeats that never reach the fast path. Returns the summary."""
    total_ms = int((time.time() - started_at) * 1000)
    by_tier = {}
    err_counter = Counter()
    for a in action_log:
        tool = a.get("tool", "?")
        tier = tier_for(tool)
        slot = by_tier.setdefault(tier, {"calls": 0, "total_ms": 0, "errors": 0})
        slot["calls"] += 1
        slot["total_ms"] += int(a.get("elapsed_ms", 0) or 0)
        rs = str(a.get("result_summary", ""))
        if rs.lower().startswith("error") or "not found" in rs.lower() or "no longer valid" in rs.lower():
            slot["errors"] += 1
            err_counter[rs[:80]] += 1
    for slot in by_tier.values():
        slot["avg_ms"] = round(slot["total_ms"] / slot["calls"], 1) if slot["calls"] else 0
    summary = {
        "ts": time.time(),
        "session_id": session_id,
        "browser_id": browser_id,
        "task": (task or "")[:200],
        "task_sig": task_sig,
        "path": path,
        "status": status,
        "completed": status == "completed",
        "total_ms": total_ms,
        "turns": turns,
        "tool_calls": len(action_log),
        "tokens_in": (tokens or {}).get("input", 0),
        "tokens_out": (tokens or {}).get("output", 0),
        "by_tier": by_tier,
        "recurring_errors": err_counter.most_common(5),
    }
    _append("tasks.jsonl", summary)
    logger.info(
        f"[browser-metrics] TASK {status} path={path} total={total_ms}ms turns={turns} "
        f"tools={len(action_log)} tok_in={summary['tokens_in']} tok_out={summary['tokens_out']} "
        f"recurring_errs={summary['recurring_errors'][:2]}"
    )
    return summary
