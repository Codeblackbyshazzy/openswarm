import type { OnboardingStep } from './types';
import { S } from '../selectors';
import { isYoutubeEnabled } from './skipPredicates';

export const step02: OnboardingStep = {
  id: 'enable_actions',
  // Demoted out of the first-run path: a feature to discover after the first win.
  stage: 'learn_features',
  index: 3,
  title: 'Enable agentic actions',
  description: 'Allow agents to work across your apps.',
  videoSrc: './onboarding-videos/v2/02.mp4',
  videoDurationLabel: '0:24',
  // Narrowed to YouTube so users with other tools still get walked.
  skipIf: isYoutubeEnabled,
  // Two beats only (open Actions, flip YouTube on); the chevron-peek and permission fine-tune popups were trimmed to give the step room to breathe.
  ops: [
    { kind: 'move_to', target: S.sidebarActions },
    { kind: 'popup', text: 'Open Actions.' },
    {
      kind: 'wait_user',
      condition: { kind: 'click_target', target: S.sidebarActions },
    },
    // YouTube on the throughline; step 3 needs it. Waits on Redux state, not click, so toggling stays synced.
    { kind: 'move_to', target: S.actionsYoutubeToggle },
    { kind: 'popup', text: 'Flip YouTube on.' },
    {
      kind: 'wait_user',
      condition: {
        kind: 'redux_predicate',
        selector: isYoutubeEnabled,
        truthy: true,
      },
      timeoutMs: 90000,
    },
    { kind: 'delay', ms: 1200 },
    { kind: 'outro' },
  ],
};
