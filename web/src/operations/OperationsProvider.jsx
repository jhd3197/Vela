import { createContext, useContext } from 'react';
import { useOperations } from './useOperations.js';

// One reader of background work for the whole dashboard.
//
// The rail's dot, the bell's count, the desk's Needs you widget and the System
// page all need the same answer, and each of them used to fetch its own part
// of it — the rail read `/api/doctor` on its own timer, the widget read it
// again on the desk's, and neither knew about a run waiting at an approval
// gate. Reading it once here is what makes them agree, and it is why adding
// the list to three more surfaces does not add three more polls.
const OperationsContext = createContext(null);

const NOTHING = {
  operations: [],
  active: [],
  needsAttention: [],
  recent: [],
  loading: false,
};

export default function OperationsProvider({ children }) {
  const operations = useOperations();
  return <OperationsContext.Provider value={operations}>{children}</OperationsContext.Provider>;
}

/**
 * Outside a provider — a fixture, a test, a page mounted on its own — this is
 * an empty list rather than a throw. A surface that decorates the interface
 * with what is happening is not the right place to discover a missing
 * provider.
 */
export function useOperationsContext() {
  return useContext(OperationsContext) || NOTHING;
}
