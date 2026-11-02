import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError } from "../lib/api";
import { Icon } from "./icons";

export type LoadState<T> =
  | { status: "loading" }
  | { status: "error"; message: string; requestId?: string; failedAt: number }
  | { status: "success"; value: T };

export type ErrorState = Extract<LoadState<never>, { status: "error" }>;

export type Reload = () => Promise<void>;

/** Load one API resource and expose a reload that ignores calls while a request is in flight. */
export function useResource<T>(fetcher: () => Promise<T>) {
  const [state, setState] = useState<LoadState<T>>({ status: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);

  const reload = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setRefreshing(true);
    try {
      const value = await fetcher();
      if (mounted.current) setState({ status: "success", value });
    } catch (error) {
      if (mounted.current) setState(toErrorState(error));
    } finally {
      inFlight.current = false;
      if (mounted.current) setRefreshing(false);
    }
  }, [fetcher]);

  useEffect(() => {
    mounted.current = true;
    void reload();
    return () => {
      mounted.current = false;
    };
  }, [reload]);

  return { state, refreshing, reload };
}

export function toErrorState(error: unknown): ErrorState {
  return {
    status: "error",
    message: error instanceof Error ? error.message : "An unexpected API error occurred.",
    requestId: error instanceof ApiError ? error.requestId : undefined,
    failedAt: Date.now(),
  };
}

export function errorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "An unexpected API error occurred.";
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return requestId ? `${message} Request ID: ${requestId}` : message;
}

export function ErrorBanner({
  subject,
  state,
  retrying = false,
  onRetry,
}: {
  subject: string;
  state: ErrorState;
  retrying?: boolean;
  onRetry?: Reload;
}) {
  return (
    // Keyed by failure time so a failed retry re-announces the alert.
    <div className="error-banner" role="alert" key={state.failedAt}>
      <span className="error-icon"><Icon name="alert" /></span>
      <div className="error-copy">
        <strong>Could not load {subject}</strong>
        <p>{state.message}{state.requestId ? ` Request ID: ${state.requestId}` : ""}</p>
        <p className="error-time">
          Last attempt failed at {new Date(state.failedAt).toLocaleTimeString()}.
        </p>
      </div>
      {onRetry && (
        <button
          type="button"
          className="retry-button"
          disabled={retrying}
          aria-busy={retrying}
          aria-label={retrying ? `Retrying ${subject}` : `Retry loading ${subject}`}
          onClick={() => void onRetry()}
        >
          {retrying ? "Retrying…" : "Retry"}
        </button>
      )}
    </div>
  );
}

export function RefreshButton({
  label,
  refreshing,
  onRefresh,
}: {
  label: string;
  refreshing: boolean;
  onRefresh: Reload;
}) {
  return (
    <button
      type="button"
      className="refresh-button"
      disabled={refreshing}
      aria-busy={refreshing}
      onClick={() => void onRefresh()}
    >
      <Icon name="refresh" className={`refresh-icon${refreshing ? " spinning" : ""}`} />
      {refreshing ? "Refreshing…" : label}
    </button>
  );
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);

  const handleCopy = async () => {
    if (!text || text.trim() === "") {
      setError(true);
      setTimeout(() => setError(false), 2000);
      return;
    }

    try {
      if (typeof navigator !== "undefined" && navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setError(false);
        setTimeout(() => setCopied(false), 2000);
      } else {
        throw new Error("Clipboard unavailable");
      }
    } catch {
      setError(true);
      setCopied(false);
      setTimeout(() => setError(false), 2000);
    }
  };

  return (
    <div className="copy-action">
      <button
        type="button"
        className={`copy-button ${copied ? "copied" : ""} ${error ? "copy-error" : ""}`}
        onClick={handleCopy}
        aria-label={label}
        title={`${label}: ${text}`}
      >
        <Icon name={copied ? "check" : error ? "alert" : "copy"} className="copy-icon" />
        <span className="copy-text">
          {copied ? "Copied" : error ? "Failed" : "Copy"}
        </span>
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {copied ? `${label} copied` : error ? `Failed to copy ${label}` : ""}
      </span>
    </div>
  );
}

const STATE_LABELS = {
  healthy: "Complete",
  incomplete: "Incomplete",
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export type BadgeState = keyof typeof STATE_LABELS;

export function StateBadge({ state }: { state: BadgeState }) {
  return (
    <span className={`state-badge state-${state}`}>
      <span aria-hidden="true" />
      {STATE_LABELS[state]}
    </span>
  );
}

/** A modal dialog that traps initial focus, closes on Escape, and restores focus on close. */
export function Dialog({
  title,
  description,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>(
      "input:not([disabled]), textarea:not([disabled]), button:not([disabled])",
    );
    (first ?? panel.current)?.focus();
    return () => previouslyFocused?.focus?.();
  }, []);

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        className={`dialog${wide ? " wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
          }
        }}
      >
        <header className="dialog-header">
          <div>
            <h2 id="dialog-title">{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button type="button" className="icon-button" aria-label="Close dialog" onClick={onClose}>
            <Icon name="close" />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let size = value;
  let unit = -1;
  do {
    size /= 1024;
    unit += 1;
  } while (size >= 1024 && unit < units.length - 1);
  return `${size.toFixed(size < 10 ? 1 : 0)} ${units[unit]}`;
}

/** Parse a `YYYYMMDD-HHMMSS-NNN` snapshot ID into its timestamp, or null if malformed. */
export function snapshotDate(snapshotId: string): Date | null {
  const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-\d{3}$/.exec(snapshotId);
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match.map(Number);
  const date = new Date(year, month - 1, day, hour, minute, second);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatSnapshotDate(snapshotId: string): string {
  const date = snapshotDate(snapshotId);
  return date
    ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : snapshotId;
}
