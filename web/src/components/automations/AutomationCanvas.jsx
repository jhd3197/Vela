import { useCallback, useEffect, useMemo, useRef } from 'react';
import { Canvas, RightRail, deriveRunState, useWorkflow } from '@tramo/editor/react';
import { createRegistry } from '@tramo/spec';
import { describeOutput } from './describeOutput.js';
import '@tramo/editor/styles.css';

// Tramo's real editor, inside Vela's shell. The registry comes from the
// server's catalog, so the picker can only offer steps Vela will also validate
// and execute. `--tr-*` overrides live in styles/components/_automation-canvas
// and are scoped to this container, not applied globally.
export default function AutomationCanvas({
  workflowId,
  catalog,
  document,
  revision,
  readOnly = false,
  onSave,
  onDocumentChange,
  events,
  onSaveStateChange,
}) {
  const registry = useMemo(
    () => createRegistry(catalog?.nodes ?? [], catalog?.integrations ?? []),
    [catalog],
  );
  // The document is loaded once per revision reset; later edits live in the
  // editor and flow back through saveDoc.
  const initial = useRef(document);
  initial.current = initial.current ?? document;
  const queue = useRef(Promise.resolve());

  const loadDoc = useCallback(() => document, [document]);

  // Saves are serialized: a debounce that fires while an earlier save is still
  // in flight must not race it, or a revision conflict becomes a lost edit.
  const saveDoc = useCallback(
    (next) => {
      if (readOnly || !onSave) return Promise.resolve();
      const task = queue.current.then(
        () => onSave(next),
        () => onSave(next),
      );
      queue.current = task.catch(() => {});
      return task;
    },
    [onSave, readOnly],
  );

  const workflow = useWorkflow({
    loadDoc,
    saveDoc: readOnly ? undefined : saveDoc,
    registry,
    key: `${workflowId}:${revision}`,
    saveDebounceMs: 700,
  });

  const { doc, saveState } = workflow;
  useEffect(() => {
    if (doc) onDocumentChange?.(doc);
  }, [doc, onDocumentChange]);
  useEffect(() => {
    onSaveStateChange?.(saveState);
  }, [saveState, onSaveStateChange]);

  // The chip under each card summarises the run. What the server stored is a
  // description of the value's shape, so turn it into a phrase rather than
  // showing the raw descriptor object.
  const run = useMemo(() => {
    const derived = deriveRunState(events ?? []);
    const statuses = {};
    for (const [nodeId, state] of Object.entries(derived.statuses)) {
      statuses[nodeId] =
        state.status === 'success'
          ? {
              status: 'success',
              durationMs: state.durationMs,
              output: describeOutput(state.output) ?? 'done',
            }
          : state;
    }
    return { ...derived, statuses };
  }, [events]);

  return (
    <div className="automation-canvas">
      <div className="automation-canvas-surface">
        <Canvas workflow={workflow} runStatus={run.statuses} runResults={undefined} />
      </div>
      <RightRail
        selection={workflow.selection}
        registry={workflow.registry}
        onApply={workflow.applyPatch}
        onClose={workflow.clearSelection}
        saveState={workflow.saveState}
        doc={workflow.doc}
      />
    </div>
  );
}
