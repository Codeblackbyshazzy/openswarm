import React from 'react';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import type { ClaudeTokens } from '@/shared/styles/claudeTokens';
import ChatBubbleTeardrop from '../ChatBubbleTeardrop';

const SHIMMER_PERIOD_S = 6;

/* Letters brighten as a wave passes: per-letter opacity keyframes are GPU-composited, unlike the old background-position gradient that repainted the text every frame. */
const GlintText: React.FC<{ text: string; slotOffset: number; totalSlots: number }> = ({ text, slotOffset, totalSlots }) => (
  <>
    {Array.from(text).map((ch, i) => (
      <Box
        key={i}
        component="span"
        sx={{
          opacity: 0.4,
          animation: `empty-state-glint ${SHIMMER_PERIOD_S}s linear infinite`,
          animationDelay: `${(((slotOffset + i) / totalSlots) * SHIMMER_PERIOD_S).toFixed(2)}s`,
          whiteSpace: 'pre',
          '@keyframes empty-state-glint': {
            '0%, 8%, 100%': { opacity: 0.4 },
            '4%': { opacity: 1 },
          },
          '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
        }}
      >
        {ch}
      </Box>
    ))}
  </>
);

const HINT_BEFORE = 'Click the ';
const HINT_AFTER = ' below to launch your first agent';
const HINT_SLOTS = HINT_BEFORE.length + 1 + HINT_AFTER.length;

const DashboardEmptyState: React.FC<{ c: ClaudeTokens }> = ({ c }) => (
  <Box
    sx={{
      position: 'absolute',
      inset: 0,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      pointerEvents: 'none',
    }}
  >
    <Typography sx={{ color: c.text.tertiary, fontSize: '1.1rem', mb: 1 }}>
      No agents running
    </Typography>
    <Typography
      sx={{
        fontSize: '0.9rem',
        display: 'inline-flex',
        alignItems: 'center',
        color: c.text.primary,
      }}
    >
      <GlintText text={HINT_BEFORE} slotOffset={0} totalSlots={HINT_SLOTS} />
      <Box component="span" sx={{ display: 'inline-flex', color: c.text.tertiary, mx: 0.35 }}>
        <ChatBubbleTeardrop sx={{ fontSize: 15 }} />
      </Box>
      <GlintText text={HINT_AFTER} slotOffset={HINT_BEFORE.length + 1} totalSlots={HINT_SLOTS} />
    </Typography>
  </Box>
);

export default DashboardEmptyState;
