import { createContext, useContext } from 'react';

// Shell services a workspace can reach without importing the shell itself,
// which keeps the page modules free of an import cycle.
export const ShellContext = createContext({ openNav: () => {} });

export function useShell() {
  return useContext(ShellContext);
}
