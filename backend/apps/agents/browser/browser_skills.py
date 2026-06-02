"""
Browser action-sequence skill cache (the "learn once, replay fast" layer),
now with cross-session persistence + text redaction.

The first time the full LLM agent completes a task, we distill the productive
action sequence and store it keyed by (host, normalized-task). A later identical
task on the same host REPLAYS that sequence with zero LLM round-trips (a ~50s
first run becomes ~1s on repeat, well under human time), and the library now
survives restarts so it keeps getting better over time.

Two properties we hold to extreme rigor:

1. CONTEXT ROT / TTFT: skills are RETRIEVAL-AS-EXECUTION, never
   retrieval-as-context. A matched skill is *run*, it is never injected into the
   prompt, so the skill library can grow to thousands of entries with ZERO
   effect on prompt size, TTFT, or context rot. Lookups are O(1) exact-key file
   reads (no corpus scan at boot or at lookup), with an in-memory hot cache, so
   cold-start and per-request latency stay flat as the library grows. And since
   a replay has zero LLM turns, it strictly REDUCES total context generated.

2. SECRETS NEVER HIT DISK: a `type` step carries the typed text, which can be a
   password / email / card / token. Any skill that touches sensitive-looking
   text (or a password-shaped field, or a tokenized URL) is kept IN-MEMORY ONLY
   and never persisted. Only fully non-sensitive skills are written to disk;
   URL userinfo + fragments are stripped before persisting regardless.

Robustness (a stale replay that "succeeds" wrongly is the ghost-failure we must
avoid): clicks are recorded by (role, name) and re-resolved fresh at replay; a
skill is only recorded if every productive step is robustly replayable; the
replay executor (in browser_agent) verifies each step and falls back to the full
LLM agent on any miss, which re-records.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# In-memory hot cache: key "host::task_sig" -> skill dict. Bounded.
_skills: dict[str, dict] = {}
_MAX_MEM_SKILLS = 200
_MAX_DISK_SKILLS = 1000          # bound the on-disk library; evict oldest by mtime
_SKILL_FORMAT_VERSION = 1

# Tools that change page state (worth replaying). Reads/meta are never recorded.
_PRODUCTIVE = {"BrowserType", "BrowserClickIndex", "BrowserClick", "BrowserPressKey", "BrowserScroll"}

_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_STOP = {
    "the", "a", "an", "to", "into", "on", "this", "that", "page", "please",
    "then", "and", "go", "open", "browser", "tell", "me", "whether", "it",
    "of", "in", "for", "with", "your", "after", "if", "you", "can",
}

# --- sensitivity detection (gate for what may touch disk) ------------------
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[ -]?){10,15}\b")
_TOKEN_PREFIX_RE = re.compile(r"\b(sk-|ghp_|gho_|pk_|xox[bap]-|AIza|eyJ)")
_SENSITIVE_FIELD_RE = re.compile(r"pass|pwd|secret|otp|cvv|cvc|ssn|card|token|api[_-]?key|security", re.I)


def _looks_sensitive(text: str, selector: str = "") -> bool:
    """Conservative: err toward 'sensitive' so secrets never persist. Catches
    emails, SSNs, card/phone-shaped digit runs, known key prefixes, long
    high-entropy tokens, and anything typed into a password-shaped field."""
    if selector and _SENSITIVE_FIELD_RE.search(selector):
        return True
    if not text:
        return False
    if _EMAIL_RE.search(text) or _SSN_RE.search(text) or _CARD_RE.search(text):
        return True
    if _TOKEN_PREFIX_RE.search(text):
        return True
    if _PHONE_RE.search(text):
        return True
    # long high-entropy token: >=20 chars with both letters and digits
    stripped = text.strip()
    if len(stripped) >= 20 and any(c.isdigit() for c in stripped) and any(c.isalpha() for c in stripped) and " " not in stripped:
        return True
    return False


def _sanitize_url(url: str) -> str:
    """Strip userinfo (user:pass@) and fragment from a URL before it persists."""
    try:
        p = urlparse(url)
        netloc = p.hostname or ""
        if p.port:
            netloc = f"{netloc}:{p.port}"
        return urlunparse((p.scheme, netloc, p.path, p.params, p.query, ""))
    except Exception:
        return url


def normalize_task(task: str) -> str:
    """Stable task signature: lowercase, drop urls/punct/filler, collapse ws."""
    t = (task or "").lower()
    t = _URL_RE.sub(" ", t)
    t = _PUNCT_RE.sub(" ", t)
    toks = [w for w in _WS_RE.sub(" ", t).strip().split(" ") if w and w not in _STOP]
    return " ".join(toks)


# --- parameterization (reuse one skill for "the same task, different input") ---
# A quoted value in the task is treated as a SLOT: it's abstracted out of the
# skill key (so `search "shoes"` and `search "hats"` share one skill) and the
# value is filled from the LIVE task at replay (so the value is never stored on
# disk, a redaction win, and the skill generalizes). Quoting is the explicit,
# high-precision signal that this token is a parameter; we never guess.
_QUOTE_RE = re.compile(r'["“”‘’\']([^"“”‘’\']{1,200})["“”‘’\']')
_SLOT_TOKEN = " slotvalue "


def template_task(task: str) -> tuple[str, list[str]]:
    """Replace each quoted span with a fixed token; return (templated, [values])."""
    values: list[str] = []

    def _repl(m):
        values.append(m.group(1))
        return _SLOT_TOKEN

    return _QUOTE_RE.sub(_repl, task or ""), values


def _sig(task: str) -> str:
    """Skill key signature: template out quoted values, then normalize, so the
    same task with different quoted inputs maps to the same key."""
    templated, _ = template_task(task)
    return normalize_task(templated)


def _parameterize(steps: list[dict], task: str) -> list[dict]:
    """Convert any BrowserType whose text is a quoted task value into a slot
    step (value_slot index), so the value is sourced live at replay, not stored."""
    _, values = template_task(task)
    if not values:
        return steps
    vlower = [v.strip().lower() for v in values]
    out = []
    for s in steps:
        if s["tool"] == "BrowserType":
            t = (s["params"].get("text") or "").strip().lower()
            if t and t in vlower:
                out.append({"tool": "BrowserType", "params": {"selector": s["params"].get("selector"), "value_slot": vlower.index(t)}})
                continue
        out.append(s)
    return out


def rehydrate(skill: dict | None, task: str) -> list[dict] | None:
    """Fill a skill's value_slot steps from the current task's quoted values.
    Returns runnable steps, or None if any slot can't be filled (caller then
    falls back to the full LLM agent, never a wrong value)."""
    if not skill:
        return None
    _, values = template_task(task)
    out = []
    for s in skill["steps"]:
        p = s.get("params", {})
        if s["tool"] == "BrowserType" and "value_slot" in p:
            idx = p["value_slot"]
            if not isinstance(idx, int) or idx < 0 or idx >= len(values):
                return None  # slot has no matching live value -> abort replay
            out.append({"tool": "BrowserType", "params": {"selector": p.get("selector"), "text": values[idx]}})
        else:
            out.append({"tool": s["tool"], "params": dict(p)})
    return out


def host_of(url: str) -> str:
    """host:port of a url (so different sites/ports never share a skill)."""
    try:
        p = urlparse(url)
        return (p.netloc or "").lower()
    except Exception:
        return ""


def distill_steps(action_log: list[dict]) -> list[dict]:
    """Turn a successful task's action_log into a robust replayable step list,
    or [] if it can't be made safely replayable."""
    steps: list[dict] = []
    productive_count = 0

    def _emit_simple(tool, inp):
        nonlocal productive_count
        if tool in ("BrowserType", "type") and inp.get("selector") is not None:
            steps.append({"tool": "BrowserType", "params": {"selector": inp.get("selector"), "text": inp.get("text", "")}})
            productive_count += 1; return True
        if tool in ("BrowserClick", "click") and inp.get("selector"):
            steps.append({"tool": "BrowserClick", "params": {"selector": inp["selector"]}})
            productive_count += 1; return True
        if tool in ("BrowserPressKey", "press_key") and inp.get("key"):
            steps.append({"tool": "BrowserPressKey", "params": {"key": inp["key"]}})
            productive_count += 1; return True
        if tool in ("BrowserScroll", "scroll"):
            steps.append({"tool": "BrowserScroll", "params": {k: inp[k] for k in ("direction", "amount") if k in inp}})
            productive_count += 1; return True
        if tool in ("BrowserNavigate", "navigate") and inp.get("url"):
            steps.append({"tool": "BrowserNavigate", "params": {"url": inp["url"]}})
            return True
        if tool in ("wait", "BrowserWait"):
            return True
        return False

    for a in action_log:
        if not a.get("ok", True):
            continue
        tool = a.get("tool")
        inp = a.get("input") or {}
        if tool == "BrowserBatch":
            subs = inp.get("actions") or []
            for sub in subs:
                st = sub.get("type")
                sp = sub.get("params") or {}
                if st == "click_index":
                    return []
                if not _emit_simple(st, sp):
                    return []
            continue
        if tool == "BrowserNavigate" and inp.get("url"):
            steps.append({"tool": "BrowserNavigate", "params": {"url": inp["url"]}})
        elif tool == "BrowserType" and inp.get("selector") is not None:
            steps.append({"tool": "BrowserType", "params": {"selector": inp.get("selector"), "text": inp.get("text", "")}})
            productive_count += 1
        elif tool == "BrowserClickIndex":
            name = a.get("clicked_name")
            if not name:
                return []
            steps.append({"tool": "BrowserClickByName", "params": {"role": a.get("clicked_role", ""), "name": name}})
            productive_count += 1
        elif tool == "BrowserClick" and inp.get("selector"):
            steps.append({"tool": "BrowserClick", "params": {"selector": inp["selector"]}})
            productive_count += 1
        elif tool == "BrowserPressKey" and inp.get("key"):
            steps.append({"tool": "BrowserPressKey", "params": {"key": inp["key"]}})
            productive_count += 1
        elif tool == "BrowserScroll":
            steps.append({"tool": "BrowserScroll", "params": {k: inp[k] for k in ("direction", "amount") if k in inp}})
            productive_count += 1
    if productive_count == 0:
        return []
    return steps


def steps_are_persistable(steps: list[dict]) -> bool:
    """True only if NO step touches sensitive text / a password-shaped field /
    a tokenized URL. Sensitive skills stay in-memory; they never hit disk."""
    for s in steps:
        p = s.get("params", {})
        if s["tool"] == "BrowserType":
            if _looks_sensitive(p.get("text", ""), p.get("selector", "")):
                return False
        elif s["tool"] == "BrowserNavigate":
            url = p.get("url", "")
            # a tokenized/credentialed URL is both sensitive and non-reproducible
            if "@" in (urlparse(url).netloc or "") or _looks_sensitive(url):
                return False
    return True


def _sanitized_steps_for_disk(steps: list[dict]) -> list[dict]:
    """Copy of steps safe to persist: navigate URLs stripped of userinfo+fragment."""
    out = []
    for s in steps:
        if s["tool"] == "BrowserNavigate":
            out.append({"tool": "BrowserNavigate", "params": {"url": _sanitize_url(s["params"].get("url", ""))}})
        else:
            out.append({"tool": s["tool"], "params": dict(s.get("params", {}))})
    return out


# --- persistence ----------------------------------------------------------
def _skills_dir() -> str | None:
    override = os.environ.get("OPENSWARM_BROWSER_SKILLS_DIR")
    base = override
    if not base:
        try:
            from backend.config.paths import DATA_ROOT
            base = os.path.join(DATA_ROOT, "browser_skills")
        except Exception:
            return None
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        return None
    return base


def _key(host: str, sig: str) -> str:
    return f"{host}::{sig}"


def _skill_path(host: str, sig: str) -> str | None:
    d = _skills_dir()
    if not d:
        return None
    h = hashlib.sha256(_key(host, sig).encode("utf-8")).hexdigest()[:32]
    return os.path.join(d, f"{h}.json")


def _persist(host: str, sig: str, skill: dict) -> None:
    """Atomic per-skill write. Best-effort; never raises. Evicts oldest on cap."""
    path = _skill_path(host, sig)
    if not path:
        return
    payload = {
        "version": _SKILL_FORMAT_VERSION,
        "host": host, "task_sig": sig,
        "steps": _sanitized_steps_for_disk(skill["steps"]),
        "recorded_at": skill.get("recorded_at", time.time()),
        "replays": skill.get("replays", 0),
    }
    try:
        d = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)  # atomic; a reader never sees a half-written file
        _evict_disk_if_over_cap(d)
    except Exception as e:
        logger.debug(f"[browser-skills] persist failed: {e}")


def _evict_disk_if_over_cap(d: str) -> None:
    try:
        files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")]
        if len(files) <= _MAX_DISK_SKILLS:
            return
        files.sort(key=lambda p: os.path.getmtime(p))  # oldest first
        for p in files[: len(files) - _MAX_DISK_SKILLS]:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def _load_from_disk(host: str, sig: str) -> dict | None:
    path = _skill_path(host, sig)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != _SKILL_FORMAT_VERSION:
            return None  # format changed -> ignore stale file
        if not data.get("steps"):
            return None
        return {
            "host": data.get("host", host), "task_sig": data.get("task_sig", sig),
            "steps": data["steps"], "recorded_at": data.get("recorded_at", 0),
            "replays": data.get("replays", 0), "persisted": True,
        }
    except Exception as e:
        logger.debug(f"[browser-skills] load failed: {e}")
        return None


def record_skill(host: str, task: str, action_log: list[dict]) -> bool:
    """Record a replayable skill. Non-sensitive skills persist to disk;
    sensitive ones stay in-memory only. Returns True if a skill was stored
    (memory or disk). Best-effort; never raises into the caller."""
    try:
        if not host:
            return False
        steps = distill_steps(action_log)
        if not steps:
            return False
        sig = _sig(task)
        if not sig:
            return False
        steps = _parameterize(steps, task)  # quoted values -> slots (not stored)
        persistable = steps_are_persistable(steps)
        skill = {
            "host": host, "task_sig": sig, "steps": steps,
            "recorded_at": time.time(), "replays": 0, "persisted": persistable,
        }
        _skills[_key(host, sig)] = skill
        if len(_skills) > _MAX_MEM_SKILLS:
            oldest = min(_skills, key=lambda k: _skills[k]["recorded_at"])
            _skills.pop(oldest, None)
        if persistable:
            _persist(host, sig, skill)
            logger.info(f"[browser-skills] recorded + PERSISTED {len(steps)}-step skill for {host}")
        else:
            logger.info(f"[browser-skills] recorded {len(steps)}-step skill for {host} (in-memory only: sensitive)")
        return True
    except Exception as e:
        logger.debug(f"[browser-skills] record failed: {e}")
        return False


def find_skill(host: str, task: str) -> dict | None:
    """Exact-key lookup: in-memory hot cache first, then a single lazy disk read
    (no corpus scan). Returns the skill or None. Cheap + flat as the library grows."""
    if not host:
        return None
    sig = _sig(task)
    if not sig:
        return None
    k = _key(host, sig)
    hit = _skills.get(k)
    if hit:
        return hit
    loaded = _load_from_disk(host, sig)
    if loaded:
        _skills[k] = loaded  # warm the hot cache
        return loaded
    return None


def mark_replayed(host: str, task: str) -> None:
    s = find_skill(host, task)
    if s:
        s["replays"] = s.get("replays", 0) + 1
        if s.get("persisted"):
            _persist(host, s["task_sig"], s)  # keep the on-disk count fresh


def list_skills(host: str) -> list[dict]:
    """Compact summaries of the skills learned for a host (task + step count +
    replay count), NOT full step dumps, so the agent can ask "what shortcuts do
    I have here?" without pulling a wall of detail into context. Reads in-memory
    + the on-disk library for this host."""
    out: dict[str, dict] = {}
    for s in _skills.values():
        if s.get("host") == host:
            out[s["task_sig"]] = {
                "task": s["task_sig"], "steps": len(s.get("steps", [])),
                "replays": s.get("replays", 0), "persisted": s.get("persisted", False),
            }
    d = _skills_dir()
    if d:
        try:
            for f in os.listdir(d):
                if not f.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        data = json.load(fh)
                except Exception:
                    continue
                sig = data.get("task_sig")
                if data.get("host") == host and sig and sig not in out:
                    out[sig] = {
                        "task": sig, "steps": len(data.get("steps", [])),
                        "replays": data.get("replays", 0), "persisted": True,
                    }
        except Exception:
            pass
    return sorted(out.values(), key=lambda x: -x["replays"])


def deprecate_skill(host: str, task: str) -> bool:
    """Remove a skill (in-memory + disk) so it stops being replayed. The agent
    calls this when it judges a saved shortcut is stale / wrong (page changed).
    Accepts either the raw task or the task_sig from list_skills (sig is
    idempotent under _sig). Returns True if something was removed."""
    if not host:
        return False
    sig = _sig(task)
    removed = _skills.pop(_key(host, sig), None) is not None
    path = _skill_path(host, sig)
    if path and os.path.exists(path):
        try:
            os.remove(path)
            removed = True
        except Exception:
            pass
    if removed:
        logger.info(f"[browser-skills] deprecated skill {host}::{sig}")
    return removed


def clear(wipe_disk: bool = False) -> None:
    """Clear the in-memory cache. With wipe_disk, also remove persisted files
    in the current skills dir (used by tests for isolation)."""
    _skills.clear()
    if wipe_disk:
        d = _skills_dir()
        if d:
            try:
                for f in os.listdir(d):
                    if f.endswith(".json"):
                        os.remove(os.path.join(d, f))
            except Exception:
                pass
