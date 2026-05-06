// Operational state sync (frontend half).
//
// Single function: sync(data). Ships whatever object the caller has.
// The cloud determines what it means. No event names, no labels,
// no analytics vocabulary. A dev sees "we sync app state."

import { API_BASE } from './config';

/** Generate an id per submit() call. Used so retries (network blip,
 *  page reload mid-flush, etc.) are deduplicated downstream rather than
 *  inserted as separate rows. Falls back to a Math.random() id on
 *  ancient browsers without crypto.randomUUID. */
function _newSubmissionId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch { /* fall through */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

let _lastTs = Date.now();
let _appStart = Date.now();

const _queue: Record<string, unknown>[] = [];
let _flushTimer: ReturnType<typeof setTimeout> | null = null;

// Bounded ring buffer of the most recent report() calls. Lets components
// (notably ErrorBoundary) attach a "what was the user doing right before
// this broke" context as a property on their own report — no extra
// outbound traffic, no extra events.
const _RECENT_CAP = 20;
const _recentReports: Array<{ s: string; a: string; ts: number }> = [];

function _record(surface: string, action: string): void {
  _recentReports.push({ s: surface, a: action, ts: Date.now() });
  if (_recentReports.length > _RECENT_CAP) {
    _recentReports.splice(0, _recentReports.length - _RECENT_CAP);
  }
}

/**
 * Snapshot the most recent N report() entries. Used by error-handling
 * paths to include "trail of breadcrumbs" context with their own report.
 */
export function getRecentActions(limit = 10): Array<{ s: string; a: string; ms_ago: number }> {
  const now = Date.now();
  const slice = _recentReports.slice(-Math.max(1, Math.min(limit, _RECENT_CAP)));
  return slice.map((r) => ({ s: r.s, a: r.a, ms_ago: now - r.ts }));
}

function _flush(): void {
  if (_queue.length === 0) return;
  const batch = _queue.splice(0);
  for (const d of batch) {
    const body = JSON.stringify(d);
    fetch(`${API_BASE}/service/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    }).catch(() => {});
  }
}

export function sync(data: Record<string, unknown> = {}, opts: { immediate?: boolean } = {}): void {
  _lastTs = Date.now();
  // Stamp a submission id + client timestamp so the cloud can deduplicate
  // retries and order events by the moment they happened, not by the
  // moment they landed.
  const stamped: Record<string, unknown> = {
    ...data,
    submission_id: typeof data.submission_id === 'string' ? data.submission_id : _newSubmissionId(),
    t: typeof data.t === 'number' ? data.t : Date.now(),
  };
  if (opts.immediate) {
    _queue.push(stamped);
    _flush();
    return;
  }
  _queue.push(stamped);
  if (_flushTimer == null) {
    _flushTimer = setTimeout(() => {
      _flushTimer = null;
      _flush();
    }, 1000);
  }
}

/**
 * Compact ship-an-event helper. Produces the same wire shape as `sync()`
 * — `{ s: surface, a: action, p: props }` — but reads as a "report a UI
 * surface event" verb in caller code rather than a free-form state dump.
 *
 * The cloud reads (surface, action) tuples from the opaque payload and
 * decides what they mean. The desktop never names what it's reporting.
 */
export function report(
  surface: string,
  action: string,
  props?: Record<string, unknown>,
  opts: { immediate?: boolean } = {},
): void {
  _record(surface, action);
  sync({ s: surface, a: action, p: props || {} }, opts);
}

export function getSessionTraceState(): {
  appStartTs: number;
  lastTs: number;
  currentPage: string;
} {
  return {
    appStartTs: _appStart,
    lastTs: _lastTs,
    currentPage: typeof window === 'undefined' ? '' : (window.location.hash || window.location.pathname),
  };
}

export function _resetForTest(): void {
  _queue.length = 0;
  if (_flushTimer != null) {
    clearTimeout(_flushTimer);
    _flushTimer = null;
  }
  _appStart = Date.now();
  _lastTs = _appStart;
  _recentReports.length = 0;
}

const serviceClient = { sync, report, getSessionTraceState, getRecentActions };
export default serviceClient;
