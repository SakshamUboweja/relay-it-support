'use client';

import { useEffect, useRef } from 'react';

/**
 * Runs `fn` every `intervalMs` while the tab is visible. A hidden tab polls
 * nothing; returning to it refreshes immediately so the view is never stale.
 */
export function usePolling(
  fn: () => void | Promise<void>,
  intervalMs: number,
  enabled = true,
) {
  const latest = useRef(fn);
  useEffect(() => {
    latest.current = fn;
  }, [fn]);
  useEffect(() => {
    if (!enabled || intervalMs <= 0) return;
    const run = () => {
      if (!document.hidden) void latest.current();
    };
    const timer = setInterval(run, intervalMs);
    const onVisibility = () => {
      if (!document.hidden) run();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [enabled, intervalMs]);
}
