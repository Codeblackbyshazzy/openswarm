import React, { useRef, useEffect, useMemo, forwardRef, useImperativeHandle, useState } from 'react';
import Box from '@mui/material/Box';
import { useElementSelection } from '@/app/components/ElementSelectionContext';
import { useIframeElementSelector } from './useIframeElementSelector';
import { getAuthToken, ensureAuthToken } from '@/shared/config';

// We render apps in a <webview> when running inside the Electron shell so
// they escape iframe restrictions (popups, mic/camera, WebAuthn,
// cross-origin fetch with cookies). Outside Electron — webpack-dev-server
// in the browser, jest, etc. — `<webview>` is a no-op element, so we fall
// back to the iframe path. Same detection BrowserCard uses.
const isElectron = navigator.userAgent.includes('Electron');

export interface ViewPreviewHandle {
  reload: () => void;
}

interface Props {
  /** URL-based serving (multi-file support). Takes priority over frontendCode. */
  serveUrl?: string;
  /** Legacy: raw HTML string rendered via srcdoc. */
  frontendCode?: string;
  inputData: Record<string, any>;
  backendResult?: Record<string, any> | null;
  style?: React.CSSProperties;
  /** Forwarded for each `console.{log,warn,error,info,debug}` inside the
   *  running app (captured by webview-preload.js → ipc-message). Only
   *  fires in the webview path — iframes have no comparable channel. */
  onConsoleMessage?: (level: string, text: string) => void;
}

function buildSrcdoc(
  frontendCode: string,
  inputData: Record<string, any>,
  backendResult: Record<string, any> | null,
): string {
  const inputJson = JSON.stringify(inputData);
  const resultJson = JSON.stringify(backendResult);

  const injection = `<script>
window.OUTPUT_INPUT = ${inputJson};
window.OUTPUT_BACKEND_RESULT = ${resultJson};
</script>`;

  if (frontendCode.includes('</head>')) {
    return frontendCode.replace('</head>', `${injection}\n</head>`);
  }
  if (frontendCode.includes('<body')) {
    return frontendCode.replace('<body', `${injection}\n<body`);
  }
  return `${injection}\n${frontendCode}`;
}

function encodeDataParam(inputData: Record<string, any>, backendResult: Record<string, any> | null): string {
  const payload = JSON.stringify({ i: inputData, r: backendResult });
  return btoa(unescape(encodeURIComponent(payload)));
}

const ViewPreview = forwardRef<ViewPreviewHandle, Props>(({
  serveUrl,
  frontendCode,
  inputData,
  backendResult = null,
  style,
  onConsoleMessage,
}, ref) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const webviewRef = useRef<any>(null);
  const ctx = useElementSelection();
  const [reloadKey, setReloadKey] = useState(0);
  // Track auth token in state so the iframe URL is rebuilt the moment the
  // token IPC roundtrip resolves. Without this, the first render runs while
  // _authTokenCache is still '' and the iframe loads a tokenless URL → 401
  // → the JSON error renders inside the preview pane.
  const [authToken, setAuthToken] = useState(() => getAuthToken());
  useEffect(() => {
    if (authToken) return;
    let cancelled = false;
    ensureAuthToken().then((tok) => {
      if (!cancelled && tok) setAuthToken(tok);
    });
    return () => { cancelled = true; };
  }, [authToken]);

  const iframeSrc = useMemo(() => {
    if (!serveUrl) return undefined;
    // Don't ship a tokenless URL — the backend auth middleware would 401 and
    // the iframe would render the JSON error. Wait for the token to load.
    if (!authToken) return undefined;
    const dataParam = encodeDataParam(inputData, backendResult);
    const sep = serveUrl.includes('?') ? '&' : '?';
    return `${serveUrl}${sep}_d=${encodeURIComponent(dataParam)}&_v=${reloadKey}&token=${encodeURIComponent(authToken)}`;
  }, [serveUrl, inputData, backendResult, reloadKey, authToken]);

  const srcdoc = useMemo(() => {
    if (serveUrl || !frontendCode) return undefined;
    return buildSrcdoc(frontendCode, inputData, backendResult);
  }, [serveUrl, frontendCode, inputData, backendResult]);

  // Use webview when (a) we're in Electron and (b) we have a real serveUrl
  // to navigate to. Inline srcdoc still goes through the iframe path: a
  // webview's only inline option is `data:text/html,...` which the Electron
  // sandbox treats as a null/opaque origin, breaking localStorage and
  // same-origin fetch for the rendered app.
  const useWebview = isElectron && !!iframeSrc;

  // Wire the iframe element into the element-selection context only when
  // we're actually rendering an iframe. A <webview>'s document lives in
  // a separate renderer process — its contentDocument is null from the
  // host page, so useIframeElementSelector's overlay/listener injection
  // can't reach it. Element selection on in-Electron previews is a known
  // regression of the webview swap.
  useEffect(() => {
    if (useWebview) return;
    if (ctx && iframeRef.current) {
      ctx.iframeRef.current = iframeRef.current;
    }
  }, [ctx, frontendCode, serveUrl, useWebview]);

  // Selector hook keys off iframeRef.current. When webview is mounted
  // instead, no <iframe> is rendered, so iframeRef.current stays null and
  // setupSelection() bails — same effect as an explicit gate.
  useIframeElementSelector(iframeRef);

  useImperativeHandle(ref, () => ({
    reload: () => {
      if (useWebview) {
        // Bumping reloadKey changes _v= in the URL, which React threads
        // back into the webview's `src` prop and re-navigates. Belt-and-
        // suspenders: also call reload() on the element in case React
        // skipped the re-render (e.g. reloadKey was already pending).
        setReloadKey(k => k + 1);
        webviewRef.current?.reload?.();
      } else if (serveUrl) {
        setReloadKey(k => k + 1);
      } else if (iframeRef.current && srcdoc) {
        iframeRef.current.srcdoc = '';
        requestAnimationFrame(() => {
          if (iframeRef.current) iframeRef.current.srcdoc = srcdoc;
        });
      }
    },
  }), [useWebview, serveUrl, srcdoc]);

  useEffect(() => {
    if (useWebview) return;
    if (iframeRef.current && srcdoc != null) {
      iframeRef.current.srcdoc = srcdoc;
    }
  }, [srcdoc, useWebview]);

  // Subscribe to the webview's ipc-message channel so the App Builder can
  // surface [FRONTEND] logs from inside the running app. The preload
  // script wraps console.* and emits 'webview-console' events; we forward
  // each one up via `onConsoleMessage`. Iframe path doesn't use this.
  useEffect(() => {
    if (!useWebview || !onConsoleMessage) return;
    const wv = webviewRef.current;
    if (!wv) return;
    const handler = (e: any) => {
      if (e?.channel !== 'webview-console') return;
      const arg = Array.isArray(e.args) ? e.args[0] : undefined;
      if (!arg) return;
      onConsoleMessage(arg.level || 'log', arg.text || '');
    };
    wv.addEventListener?.('ipc-message', handler);
    return () => {
      try { wv.removeEventListener?.('ipc-message', handler); } catch (_e) {}
    };
  }, [useWebview, onConsoleMessage, iframeSrc]);

  const hasContent = !!(serveUrl || frontendCode?.trim());

  if (!hasContent) {
    return (
      <Box
        sx={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#888',
          fontSize: '0.85rem',
          fontStyle: 'italic',
          ...style,
        }}
      >
        No preview available
      </Box>
    );
  }

  const selectActive = ctx?.selectMode ?? false;

  return (
    <Box
      sx={{
        width: '100%',
        height: '100%',
        position: 'relative',
        ...(selectActive && {
          '&::after': {
            content: '""',
            position: 'absolute',
            inset: 0,
            border: '2px solid #3b82f6',
            borderRadius: '2px',
            pointerEvents: 'none',
            animation: 'selectModePulse 2s ease-in-out infinite',
            zIndex: 1,
          },
          '@keyframes selectModePulse': {
            '0%, 100%': { borderColor: 'rgba(59, 130, 246, 0.6)' },
            '50%': { borderColor: 'rgba(59, 130, 246, 0.2)' },
          },
        }),
      }}
    >
      {useWebview ? (
        <webview
          ref={(el: any) => { webviewRef.current = el; }}
          // Stable key so React swaps src in place rather than remounting
          // — preserves the prior frame's pixels through reload, same
          // pattern as the iframe path.
          key="url-mode-webview"
          src={iframeSrc}
          // Autoplay is the most common cross-app expectation; matches
          // the BrowserCard default. Plugins / nodeintegration stay off.
          webpreferences="autoplayPolicy=no-user-gesture-required"
          style={{
            width: '100%',
            height: '100%',
            border: 'none',
            background: '#fff',
            ...style,
          }}
        />
      ) : (
        <iframe
          ref={iframeRef}
          // Key stable across reloads — only changes when switching MODES
          // (URL vs srcdoc). Previously the key embedded reloadKey, which
          // unmounted-and-remounted the iframe on every reload, producing
          // a visible blank flash mid-burst. With a stable key, reloadKey
          // still updates iframeSrc → React swaps the src attribute on
          // the EXISTING iframe element → browser navigates in place,
          // keeping the prior frame's pixels visible until the new doc
          // paints. No flash.
          key={iframeSrc ? 'url-mode' : 'srcdoc'}
          src={iframeSrc}
          sandbox="allow-scripts allow-same-origin"
          style={{
            width: '100%',
            height: '100%',
            border: 'none',
            background: '#fff',
            ...style,
          }}
          title="App Preview"
        />
      )}
    </Box>
  );
});

export default ViewPreview;
