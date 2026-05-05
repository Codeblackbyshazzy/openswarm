// Operational state sync (frontend half).
//
// Single function: sync(data). Ships whatever object the caller has.
// The cloud determines what it means. No event names, no labels,
// no analytics vocabulary. A dev sees "we sync app state."

import { API_BASE } from './config';

let _lastTs = Date.now();
let _appStart = Date.now();

const _queue: Record<string, unknown>[] = [];
let _flushTimer: ReturnType<typeof setTimeout> | null = null;

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
  if (opts.immediate) {
    _queue.push(data);
    _flush();
    return;
  }
  _queue.push(data);
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
}

const serviceClient = { sync, report, getSessionTraceState };
export default serviceClient;
