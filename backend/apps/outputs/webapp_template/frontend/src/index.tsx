// Install window.OPENSWARM_APP BEFORE anything else so the agent bridge exists
// from first paint, even while React + the app are still mounting. The app fills
// it in by calling window.OPENSWARM_APP.register(...) on mount.
import './agentBridge';
import React from 'react';
import { createRoot } from 'react-dom/client';
import Main from './app/Main';
import ErrorBoundary from './app/components/ErrorBoundary';

console.log('[App] Bootstrapping React app');
const rootEl = document.getElementById('root');
if (!rootEl) {
  console.error('[App] FATAL: #root element not found in DOM');
} else {
  // Wrap Main in an ErrorBoundary so any runtime crash from agent
  // edits (missing imports, hook-rules violations, etc.) shows a
  // readable error card in the preview pane instead of unmounting
  // to a blank screen. The boundary also forwards the error via
  // console.error + postMessage so the agent sees it on its next
  // turn.
  createRoot(rootEl).render(
    <ErrorBoundary>
      <Main />
    </ErrorBoundary>,
  );
  console.log('[App] React root mounted');
}
