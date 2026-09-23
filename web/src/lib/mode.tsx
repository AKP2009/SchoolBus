import { createContext, useContext, useEffect, type ReactNode } from 'react';

export type Mode = 'cab' | 'office';

const ModeContext = createContext<Mode>('office');

export function useMode(): Mode {
  return useContext(ModeContext);
}

/** Sets data-mode on <html> while a route layout is mounted (cab for /operator, office for /manager). */
export function useDocumentMode(mode: Mode): void {
  useEffect(() => {
    const root = document.documentElement;
    const previous = root.dataset.mode;
    root.dataset.mode = mode;
    return () => {
      if (previous) root.dataset.mode = previous;
    };
  }, [mode]);
}

/**
 * Provides the mode to components. With `scoped`, also puts data-mode on a wrapper element so a
 * subtree can use the other palette (the styleguide shows both modes side by side).
 */
export function ModeProvider({
  mode,
  scoped = false,
  className,
  children,
}: {
  mode: Mode;
  scoped?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <ModeContext.Provider value={mode}>
      {scoped ? (
        <div data-mode={mode} className={className}>
          {children}
        </div>
      ) : (
        children
      )}
    </ModeContext.Provider>
  );
}
