// Docked onboarding home: a compact row at the bottom of the sidebar that springs the tour back open.

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Box,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  IconButton,
  Typography,
  CircularProgress,
} from '@mui/material';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import CloseIcon from '@mui/icons-material/Close';
import { useClaudeTokens } from '@/shared/styles/ThemeContext';
import { useOnboardingProgress } from './hooks/useOnboardingProgress';
import { STEPS } from './steps';
import { report } from './telemetry';

const OnboardingSidebarEntry: React.FC = () => {
  const c = useClaudeTokens();
  const progress = useOnboardingProgress();
  const [hovered, setHovered] = useState(false);

  const total = STEPS.length;
  const done = progress.completedSteps.length;
  const show =
    progress.initialized && progress.panelMode === 'docked' && done < total;
  const pct = total > 0 ? (done / total) * 100 : 0;

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          key="onboarding-docked"
          initial={{ opacity: 0, scale: 0.85, y: 6 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.9, y: 6 }}
          transition={{ type: 'spring', stiffness: 380, damping: 30 }}
        >
          <Box sx={{ px: 1, pt: 1 }}>
            <ListItemButton
              onMouseEnter={() => setHovered(true)}
              onMouseLeave={() => setHovered(false)}
              onClick={() => {
                report('panel_reopened', { from: 'sidebar' });
                progress.setPanelMode('expanded');
              }}
              sx={{
                borderRadius: 1.5,
                py: 0.6,
                px: 1.25,
                bgcolor: `${c.accent.primary}0C`,
                '&:hover': { bgcolor: `${c.accent.primary}1A` },
                transition: 'background-color 0.15s',
              }}
            >
              <ListItemIcon
                sx={{ minWidth: 32, position: 'relative', display: 'inline-flex' }}
              >
                <CircularProgress
                  variant="determinate"
                  value={100}
                  size={20}
                  thickness={4}
                  sx={{ color: `${c.text.tertiary}33` }}
                />
                <CircularProgress
                  variant="determinate"
                  value={pct}
                  size={20}
                  thickness={4}
                  sx={{ color: c.accent.primary, position: 'absolute', left: 0 }}
                />
              </ListItemIcon>
              <ListItemText
                primary="Finish setup"
                sx={{
                  '& .MuiListItemText-primary': {
                    color: c.text.primary,
                    fontSize: '0.82rem',
                    fontWeight: 500,
                  },
                }}
              />
              {hovered ? (
                <IconButton
                  size="small"
                  aria-label="Dismiss setup"
                  onClick={(e) => {
                    e.stopPropagation();
                    report('panel_dismissed', { from: 'sidebar' });
                    progress.setPanelMode('hidden');
                  }}
                  sx={{
                    p: 0.2,
                    color: c.text.tertiary,
                    '&:hover': { color: c.text.primary },
                  }}
                >
                  <CloseIcon sx={{ fontSize: 15 }} />
                </IconButton>
              ) : (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.3 }}>
                  <Typography
                    sx={{ fontSize: '0.72rem', color: c.text.muted, fontWeight: 500 }}
                  >
                    {done}/{total}
                  </Typography>
                  <ChevronRightIcon sx={{ fontSize: 16, color: c.text.tertiary }} />
                </Box>
              )}
            </ListItemButton>
          </Box>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default OnboardingSidebarEntry;
