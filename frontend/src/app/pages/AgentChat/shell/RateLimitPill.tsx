import React, { useEffect, useRef } from 'react';
import Box from '@mui/material/Box';
import Fade from '@mui/material/Fade';
import Typography from '@mui/material/Typography';
import ScheduleIcon from '@mui/icons-material/Schedule';
import { useAppDispatch, useAppSelector } from '@/shared/hooks';
import { clearRateLimited } from '@/shared/state/agentsSlice';
import { useClaudeTokens } from '@/shared/styles/ThemeContext';

// Muted, transient pill shown only after a real provider throttle outlasted the silent backoff. No card, no red, no CTA; it fades and auto-clears once the window should have passed. The "why" lives in the hover, not on the surface.
export const RateLimitPill: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  const c = useClaudeTokens();
  const dispatch = useAppDispatch();
  const rl = useAppSelector((s) => s.agents.sessions[sessionId]?.rate_limited);

  useEffect(() => {
    if (!rl) return;
    const ms = Math.min(Math.max(rl.retry_after_s ?? 45, 5), 300) * 1000;
    const t = setTimeout(() => dispatch(clearRateLimited({ sessionId })), ms);
    return () => clearTimeout(t);
  }, [rl, sessionId, dispatch]);

  const label = rl?.retry_after_s
    ? `Back ~${(() => {
        const d = new Date(Date.now() + rl.retry_after_s * 1000);
        return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
      })()}`
    : 'Rate limited';
  // Hold the last text so the exit fade renders content, not a blank pill.
  const lastLabel = useRef(label);
  if (rl) lastLabel.current = label;

  return (
    <Fade in={!!rl} timeout={{ enter: 200, exit: 220 }} unmountOnExit>
      <Box
        title="Your plan hit its rate limit, it'll resume on its own"
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 0.6,
          alignSelf: 'flex-start',
          mx: 2,
          mb: 1,
          px: 1.25,
          py: 0.5,
          borderRadius: 999,
          bgcolor: c.bg.secondary,
          color: c.text.tertiary,
        }}
      >
        <ScheduleIcon sx={{ fontSize: 14 }} />
        <Typography sx={{ fontSize: '0.75rem', fontWeight: 500 }}>{lastLabel.current}</Typography>
      </Box>
    </Fade>
  );
};
