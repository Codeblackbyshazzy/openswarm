// window.OPENSWARM_APP - the agent bridge, shipped with the template so it
// EXISTS from first paint, before any app-specific code runs (index.tsx imports
// this first). An app makes itself agent-operable by calling
// window.OPENSWARM_APP.register({ rules, controls, getState, invoke }) on mount;
// it never has to wire up the plumbing, so it cannot forget it. Until the app
// registers, describe()/getState() report { __ready: false } so the agent knows
// the app is still booting (and waits) instead of declaring it bridge-less.

export type AgentControl = {
  name: string;
  args?: Record<string, unknown>;
  description?: string;
  keys?: string; // optional key hint, e.g. "Space = flap", "WASD to move"
};

export type AgentRegistration = {
  rules?: string; // what the app is and its objective, plain prose
  controls: AgentControl[] | (() => AgentControl[]); // a function for dynamic controls
  getState?: () => unknown; // small JSON snapshot, used to verify an action landed
  invoke: (name: string, args?: Record<string, unknown>) => unknown;
};

type Bridge = {
  __openswarm: true;
  __ready: boolean;
  __rev: number;
  register: (api: AgentRegistration) => void;
  refresh: () => void; // bump __rev after dynamic controls change so the agent re-reads
  describe: () => unknown;
  getState: () => unknown;
  invoke: (name: string, args?: Record<string, unknown>) => unknown;
};

declare global {
  interface Window {
    OPENSWARM_APP?: Bridge;
  }
}

let registration: AgentRegistration | null = null;

function resolveControls(): AgentControl[] {
  if (!registration) return [];
  const c = registration.controls;
  try {
    return typeof c === 'function' ? c() || [] : c || [];
  } catch {
    return [];
  }
}

const bridge: Bridge = {
  __openswarm: true,
  __ready: false,
  __rev: 0,
  register(api: AgentRegistration) {
    registration = api;
    bridge.__ready = true;
    bridge.__rev += 1;
  },
  refresh() {
    bridge.__rev += 1;
  },
  describe() {
    if (!bridge.__ready || !registration) {
      return { __ready: false, __rev: bridge.__rev };
    }
    return {
      rules: registration.rules || '',
      controls: resolveControls(),
      __rev: bridge.__rev,
    };
  },
  getState() {
    if (!bridge.__ready || !registration) {
      return { __ready: false, __rev: bridge.__rev };
    }
    let state: unknown = {};
    try {
      state = registration.getState ? registration.getState() : {};
    } catch (e) {
      return { __error__: String((e as Error)?.message || e), __rev: bridge.__rev };
    }
    // Carry __rev alongside the app's own state so the agent can detect a
    // controls change with a single getState, without re-describing every turn.
    if (state && typeof state === 'object' && !Array.isArray(state)) {
      return { ...(state as Record<string, unknown>), __rev: bridge.__rev };
    }
    return { value: state, __rev: bridge.__rev };
  },
  invoke(name: string, args?: Record<string, unknown>) {
    if (!bridge.__ready || !registration) {
      throw 'OPENSWARM_APP not registered yet';
    }
    return registration.invoke(name, args || {});
  },
};

window.OPENSWARM_APP = bridge;
