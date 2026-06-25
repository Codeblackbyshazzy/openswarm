import type { Dispatch } from '@reduxjs/toolkit';
import { removeBrowserCard } from '@/shared/state/dashboardLayoutSlice';
import { getBrowserWebviews } from '@/shared/browserRegistry';

interface CdpBridge {
  cdpDetachClean?: (wcId: number) => Promise<unknown>;
}

// A wedged CDP pipe must never hold a card open; cap the whole detach round-trip.
const DETACH_BUDGET_MS = 600;

// Detach the CDP debugger from every webview of a browser card BEFORE React unmounts them. Otherwise a late DevTools notification (a Target child-session message or Network.responseReceivedExtraInfo) lands on a session Chromium has already freed and SIGSEGVs the whole browser process. Bounded + fail-open.
export async function detachBrowserCdp(browserId: string): Promise<void> {
  const ow = (window as unknown as { openswarm?: CdpBridge }).openswarm;
  if (!ow?.cdpDetachClean) return;
  const detaches: Promise<unknown>[] = [];
  for (const wv of getBrowserWebviews(browserId)) {
    try {
      detaches.push(ow.cdpDetachClean(wv.getWebContentsId()).catch(() => {}));
    } catch {
      // webview already torn down; nothing to detach
    }
  }
  if (!detaches.length) return;
  await Promise.race([
    Promise.allSettled(detaches),
    new Promise<void>((resolve) => setTimeout(resolve, DETACH_BUDGET_MS)),
  ]);
}

// Clean-detach a browser card's CDP, THEN remove it. All three card-removal paths (the X button, the agent-finish timer, and keyboard delete) route through here so none of them tears the webview down with the debugger still attached. The detach is bounded, so removal is never blocked by a dead pipe.
export async function removeBrowserCardCleanly(
  browserId: string,
  dispatch: Dispatch,
): Promise<void> {
  await detachBrowserCdp(browserId);
  dispatch(removeBrowserCard(browserId));
}
