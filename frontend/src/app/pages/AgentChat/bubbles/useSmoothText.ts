import { useEffect, useRef, useState } from 'react';

/**
 * Smoothly reveals streamed text at a steady cadence instead of painting bursty
 * network chunks as they land. Decouples DISPLAY rate from ARRIVAL rate the way
 * claude.ai does, so generated text reads like it's being typed rather than
 * dumped in clumps.
 *
 * Zero dependencies. Zero added TTFT: the first characters reveal on the very next
 * animation frame after the first delta (same frame budget as painting it directly).
 * The reveal rate is ADAPTIVE — it accelerates as the backlog grows, so display
 * never falls meaningfully behind the model and never reads as laggy. The rAF loop
 * runs ONLY while there's a backlog to drain and parks itself at zero cost once
 * caught up, so it adds no idle-frame churn.
 */

/** Pure pacing step (exported for testing): chars to reveal this frame. */
export function smoothStep(shown: number, full: number): number {
  if (shown >= full) return full;
  const backlog = full - shown;
  // Floor of 3 chars/frame (~180 chars/sec at 60fps) for a calm typing feel,
  // and drain ~1/4 of any backlog on top of that so bursts catch up fast. The
  // /4 keeps mid-stream lag small (a few words at most), so when the live bubble
  // hands off to the final message at stream end there's no visible jump. Never
  // overshoots `full`.
  const step = Math.max(3, Math.ceil(backlog / 4));
  return Math.min(full, shown + step);
}

export function useSmoothText(target: string, enabled: boolean): string {
  const [shownLen, setShownLen] = useState(enabled ? 0 : target.length);
  const rafRef = useRef<number | null>(null);
  const targetRef = useRef(target);
  targetRef.current = target;

  useEffect(() => {
    // Disabled (historical message, or smoothing turned off): show all, stop loop.
    if (!enabled) {
      if (rafRef.current != null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
      setShownLen(targetRef.current.length);
      return;
    }
    const tick = () => {
      rafRef.current = null;
      setShownLen((cur) => {
        const next = smoothStep(cur, targetRef.current.length);
        if (next < targetRef.current.length) rafRef.current = requestAnimationFrame(tick);
        return next;
      });
    };
    // Start a drain only if we're behind and no loop is already running.
    if (rafRef.current == null && shownLen < target.length) {
      rafRef.current = requestAnimationFrame(tick);
    }
    return () => {
      if (rafRef.current != null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    };
  }, [enabled, target.length, shownLen]);

  // Target shrank (new turn / reset / branch switch): re-sync so we don't slice
  // past the end of a shorter string.
  useEffect(() => {
    if (shownLen > target.length) setShownLen(enabled ? 0 : target.length);
  }, [target.length, shownLen, enabled]);

  // ZERO added TTFT: on the very first frame content exists (shownLen still 0),
  // reveal the floor immediately in-render instead of waiting a frame for the rAF
  // tick. Pure derivation, no extra render — so first visible text lands on the
  // exact same frame it would have without smoothing. State catches up next frame.
  if (!enabled) return target;
  const effectiveShown = (shownLen === 0 && target.length > 0)
    ? Math.min(3, target.length)
    : shownLen;
  return target.slice(0, effectiveShown);
}
