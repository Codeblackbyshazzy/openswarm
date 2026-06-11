// Soft, earned unlocks for the onboarding panel. A locked step is still fully
// usable in the app, this only gates the guided spotlight + shows a lock icon
// with a one-line teaser, so the tour reveals things in an order you earn by
// doing real actions. Unlocks fire off the same milestone predicates the
// skipIf scanner uses, so exploring (e.g. opening a browser yourself) unlocks
// the next thing immediately, never punished for going off-script.

import { useMemo } from 'react';
import type { RootState } from '@/shared/state/store';
import { useAppSelector } from '@/shared/hooks';
import { hasAnyAgentLaunched, hasAnyBrowserSpawned } from './skipPredicates';
import { STEPS } from './index';

interface UnlockRule {
  by: (s: RootState) => boolean;
  hint: string;
}

// Steps without a rule are unlocked from the start (the get-started entry points).
const RULES: Record<string, UnlockRule> = {
  enable_actions: { by: hasAnyAgentLaunched, hint: 'Run your first agent' },
  use_browser: { by: hasAnyAgentLaunched, hint: 'Run your first agent' },
  install_skill: { by: hasAnyAgentLaunched, hint: 'Run your first agent' },
  make_app: { by: hasAnyAgentLaunched, hint: 'Run your first agent' },
  agent_control_agents: { by: hasAnyAgentLaunched, hint: 'Run your first agent' },
  agent_use_browser: { by: hasAnyBrowserSpawned, hint: 'Open a browser' },
};

export function isStepUnlocked(stepId: string, s: RootState): boolean {
  const rule = RULES[stepId];
  return rule ? rule.by(s) : true;
}

export function unlockHintFor(stepId: string): string | null {
  return RULES[stepId]?.hint ?? null;
}

/** Set of currently-unlocked step ids. Keyed on a stable string so the selector
 *  only re-renders when the unlock set actually changes. */
export function useUnlockedStepIds(): Set<string> {
  const key = useAppSelector((s) =>
    STEPS.filter((st) => isStepUnlocked(st.id, s)).map((st) => st.id).join('|'),
  );
  return useMemo(() => new Set(key ? key.split('|') : []), [key]);
}
