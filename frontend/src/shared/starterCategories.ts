import { Search, Hammer, Globe, Plug } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

// Two-level starters shared by the empty-state and the first-run welcome chat: pick a
// category, then a concrete prompt. These are chosen to SHOWCASE what makes OpenSwarm
// different from a plain chatbot, real apps (App Builder), the browser agent, your own
// tools (MCPs), the file harness (PDFs/exports), and parallel agents on the canvas. Every
// prompt is one-click-runnable (no [placeholders]) and reads plainly for a non-dev.
// target 'app-builder' opens the App Builder (live preview); the rest run as an agent.
export type StarterCategory = {
  id: string;
  label: string;
  Icon: LucideIcon;
  prompts: string[];
  target?: 'app-builder';
};

export const STARTER_CATEGORIES: StarterCategory[] = [
  {
    // Web research that ends in a real artifact + parallel agents on the canvas.
    id: 'research', label: 'Research', Icon: Search,
    prompts: [
      'Plan a 3-day Tokyo trip and turn it into a printable PDF itinerary',
      'Compare the 5 best robot vacuums and make me a one-page buying guide',
      'Spin up 3 agents to research 3 competitors at once and tell me who wins',
      "Find the latest on a topic I'll name and write me a brief with real sources",
    ],
  },
  {
    // The App Builder is a full app (logic + data + live preview), not a toy snippet.
    id: 'build', label: 'Build an app', Icon: Hammer, target: 'app-builder',
    prompts: [
      'Build a working expense tracker app with live charts',
      'Make a Snake game I can actually play right now',
      'Build a habit tracker that remembers my streaks between visits',
      'Build a mini Minecraft I can walk around in',
    ],
  },
  {
    // The browser agent: OpenSwarm's most powerful tool, it actually drives the web.
    id: 'browse', label: 'Use the web', Icon: Globe,
    prompts: [
      'Send an agent to find the cheapest flights to Tokyo and show me the best options',
      'Have an agent pull a clean list of the top-rated coffee shops in a city',
      'Find 3 well-reviewed standing desks online and screenshot the best one',
      'Watch an agent sign me up for a free newsletter on a site',
    ],
  },
  {
    // MCPs: plug your real tools in and let agents work across them.
    id: 'connect', label: 'Connect your apps', Icon: Plug,
    prompts: [
      'Summarize my Gmail inbox and flag what actually needs a reply',
      'Turn my Notion notes into a clear action plan',
      'Look at my calendar and lay out a realistic plan for my week',
      'Pull a sheet from my Google Drive and chart what matters',
    ],
  },
];
