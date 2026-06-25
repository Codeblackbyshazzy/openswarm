export type AppMode = 'home' | 'calendar' | 'detail' | 'new' | 'trash';
export type CalView = 'week' | 'month';

// Navigation + ephemeral UI state for the Workflows app window. Data lives in Redux; this is only "where am I looking right now".
export interface AppNav {
  mode: AppMode;
  selectedId: string | null;
  calView: CalView;
  refDate: Date;
  goHome: () => void;
  goCalendar: () => void;
  goNew: () => void;
  goTrash: () => void;
  selectWorkflow: (id: string) => void;
  setCalView: (v: CalView) => void;
  setRefDate: (d: Date) => void;
}
