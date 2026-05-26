import React, { RefObject } from 'react';
import Box from '@mui/material/Box';
import { ClaudeTokens } from '@/shared/styles/claudeTokens';

interface Props {
  c: ClaudeTokens;
  editorRef: RefObject<HTMLDivElement>;
  disabled?: boolean;
  hasContent: boolean;
  hasAttachments: boolean;
  autoRunMode?: boolean;
  isRunning?: boolean;
  queueLength: number;
  placeholderLabel: string;
  onInput: () => void;
  onClick: () => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  onPaste: (e: React.ClipboardEvent) => void;
}

// Windows-only ablation: on Chromium 144 + Castlabs Electron 40, mounting a <div contentEditable> initializes the Windows Text Services Framework (TSF) edit-context shim which segfaults during React commit (0xC0000005). Plain <textarea> goes through a different native path and doesn't engage the same TSF shim. Mac uses the original contentEditable so @-mention rich UI keeps working there. ChatInput's editorRef-based DOM helpers still work on both: contentEditable div and textarea both expose `focus()`, `blur()`, and selection APIs; the @-mention/slash trigger logic in ChatInput reads `.textContent` / `.innerText` for div and falls through to `.value` for textarea via duck-typed access.
const IS_WIN = typeof navigator !== 'undefined' && navigator.userAgent.includes('Windows');

export const EditorSurface: React.FC<Props> = ({
  c, editorRef, disabled, hasContent, hasAttachments, autoRunMode, isRunning, queueLength,
  placeholderLabel, onInput, onClick, onKeyDown, onPaste,
}) => {
  // eslint-disable-next-line no-console
  console.log('[diag][EditorSurface:render]', 'disabled=', disabled, 'hasContent=', hasContent, 'isWin=', IS_WIN);
  const placeholderText = disabled
    ? 'Agent is working...'
    : autoRunMode
      ? 'Describe what data to generate…'
      : isRunning
        ? (queueLength > 0 ? `${queueLength} queued, type another or wait…` : 'Agent is working, messages will queue…')
        : placeholderLabel;

  if (IS_WIN) {
    return (
      <Box sx={{ px: 1.5, pt: hasAttachments ? 0.5 : 1.25, pb: 0.25, position: 'relative' }}>
        <textarea
          ref={editorRef as unknown as React.RefObject<HTMLTextAreaElement>}
          data-onboarding="chat-input"
          disabled={!!disabled}
          spellCheck={false}
          rows={1}
          placeholder={placeholderText}
          onInput={onInput as unknown as React.FormEventHandler<HTMLTextAreaElement>}
          onClick={onClick as unknown as React.MouseEventHandler<HTMLTextAreaElement>}
          onKeyDown={onKeyDown as unknown as React.KeyboardEventHandler<HTMLTextAreaElement>}
          onPaste={onPaste as unknown as React.ClipboardEventHandler<HTMLTextAreaElement>}
          style={{
            width: '100%',
            minHeight: '1.5em',
            maxHeight: 220,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            color: c.text.primary,
            fontSize: '0.95rem',
            lineHeight: '1.55',
            fontFamily: 'inherit',
            wordBreak: 'break-word',
            whiteSpace: 'pre-wrap',
            resize: 'none',
            padding: 0,
          }}
        />
      </Box>
    );
  }

  return (
    <Box sx={{ px: 1.5, pt: hasAttachments ? 0.5 : 1.25, pb: 0.25, position: 'relative' }}>
      <div
        ref={editorRef}
        data-onboarding="chat-input"
        contentEditable={!disabled}
        suppressContentEditableWarning
        spellCheck={false}
        onInput={onInput}
        onClick={onClick}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        style={{
          width: '100%',
          minHeight: '1.5em',
          maxHeight: 220,
          overflowY: 'auto',
          background: 'transparent',
          border: 'none',
          outline: 'none',
          color: c.text.primary,
          fontSize: '0.95rem',
          lineHeight: '1.55',
          fontFamily: 'inherit',
          wordBreak: 'break-word',
          whiteSpace: 'pre-wrap',
        }}
      />
      {!hasContent && (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            padding: `${hasAttachments ? 4 : 10}px 12px`,
            color: c.text.tertiary,
            fontSize: '0.95rem',
            lineHeight: '1.5',
            fontFamily: 'inherit',
            pointerEvents: 'none',
            userSelect: 'none',
          }}
        >
          {placeholderText}
        </div>
      )}
    </Box>
  );
};
