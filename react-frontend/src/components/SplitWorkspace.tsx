import { ReactNode } from 'react';

import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';

interface SplitWorkspaceProps {
  /**
   * Unique ID used by react-resizable-panels to persist the split ratio
   * across reloads via localStorage. The Assistant and Voice pages pass
   * the same ID so the divider position stays consistent across pages —
   * dragging it on one page is reflected on the other.
   */
  storageId: string;
  left: ReactNode;
  right: ReactNode;
  /**
   * Initial width of the left panel as a percentage. Defaults to an even
   * 50/50 split, which suits the Assistant/Voice pages where both sides
   * carry equal weight. A list/detail layout (the Data explorer) passes a
   * smaller value so the reading pane gets the space.
   *
   * The right panel's constraints are DERIVED from these three values so the
   * two panels' defaultSize always sums to 100. react-resizable-panels
   * renormalizes when it doesn't, and the far panel's maxSize then pushes the
   * leftover into the near panel — which silently inverts the intended ratio.
   */
  defaultLeftSize?: number;
  /** Lower bound for the left panel, in percent. */
  minLeftSize?: number;
  /** Upper bound for the left panel, in percent. */
  maxLeftSize?: number;
}

/**
 * Two-panel horizontal layout with a drag-to-resize handle between them.
 *
 * - Each panel is bounded to 25-75% so neither side collapses too small.
 * - Default split is 50/50 on first load; user's chosen ratio persists.
 * - The handle is a thin vertical bar that widens on hover/drag and
 *   surfaces a subtle gradient grip mark.
 */
export function SplitWorkspace({
  storageId,
  left,
  right,
  defaultLeftSize = 50,
  minLeftSize = 25,
  maxLeftSize = 75,
}: SplitWorkspaceProps) {
  // Mirror the left panel's constraints so both panels always describe the
  // same 100% budget. With the defaults this is exactly 50/25/75 on each side.
  const defaultRightSize = 100 - defaultLeftSize;
  const minRightSize = 100 - maxLeftSize;
  const maxRightSize = 100 - minLeftSize;

  return (
    <PanelGroup
      direction="horizontal"
      autoSaveId={storageId}
      className="h-full w-full"
    >
      <Panel defaultSize={defaultLeftSize} minSize={minLeftSize} maxSize={maxLeftSize}>
        <div className="h-full overflow-y-auto min-w-0">{left}</div>
      </Panel>
      <PanelResizeHandle className="group/handle relative flex w-1 shrink-0 cursor-col-resize items-stretch transition-[width,background] duration-150 hover:w-1.5 data-[resize-handle-active]:w-1.5">
        <span className="block w-full bg-border transition-colors duration-150 group-hover/handle:bg-brand-2/50 group-data-[resize-handle-active]/handle:bg-brand-2/70" />
        <span
          className="pointer-events-none absolute left-1/2 top-1/2 h-8 -translate-x-1/2 -translate-y-1/2 rounded-full opacity-0 transition-opacity duration-150 group-hover/handle:opacity-100 group-data-[resize-handle-active]/handle:opacity-100"
          aria-hidden
          style={{
            width: 4,
            background:
              'linear-gradient(to bottom, rgb(var(--brand-1)), rgb(var(--brand-2)), rgb(var(--brand-3)))',
          }}
        />
      </PanelResizeHandle>
      <Panel defaultSize={defaultRightSize} minSize={minRightSize} maxSize={maxRightSize}>
        <div className="h-full overflow-y-auto min-w-0">{right}</div>
      </Panel>
    </PanelGroup>
  );
}
