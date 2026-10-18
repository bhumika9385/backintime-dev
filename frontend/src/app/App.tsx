import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { ApiError, api } from "../lib/api";
import { Icon, type IconName } from "./icons";
import type { Health, Profile, SystemInfo } from "../types/api";

const CONFIGURATION_DOCS_URL = "https://github.com/timevault-dev/BackInTime#getting-started";

type LoadState<T> =
  | { status: "loading" }
  | { status: "error"; message: string; requestId?: string; failedAt: number }
  | { status: "success"; value: T };

type Reload = () => Promise<void>;

const loadProfiles = async () => (await api.profiles()).profiles;

/** Load one API resource and expose a reload that ignores calls while a request is in flight. */
function useResource<T>(fetcher: () => Promise<T>) {
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

function useBackend() {
  const health = useResource<Health>(api.health);
  const system = useResource<SystemInfo>(api.systemInfo);
  const profiles = useResource<Profile[]>(loadProfiles);
  const reloadHealth = health.reload;
  const reloadSystem = system.reload;
  const reloadProfiles = profiles.reload;
  const reloadAll = useCallback(async () => {
    await Promise.all([reloadHealth(), reloadSystem(), reloadProfiles()]);
  }, [reloadHealth, reloadSystem, reloadProfiles]);

  return {
    health: health.state,
    system: system.state,
    profiles: profiles.state,
    refreshing: {
      health: health.refreshing,
      system: system.refreshing,
      profiles: profiles.refreshing,
      any: health.refreshing || system.refreshing || profiles.refreshing,
    },
    reload: { health: reloadHealth, system: reloadSystem, profiles: reloadProfiles, all: reloadAll },
  };
}

function toErrorState(error: unknown): Extract<LoadState<never>, { status: "error" }> {
  return {
    status: "error",
    message: error instanceof Error ? error.message : "An unexpected API error occurred.",
    requestId: error instanceof ApiError ? error.requestId : undefined,
    failedAt: Date.now(),
  };
}

function App() {
  const backend = useBackend();
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);

  const profilesList = backend.profiles.status === "success" ? backend.profiles.value : [];
  const activeProfileId =
    selectedProfileId && profilesList.some((p) => p.profile_id === selectedProfileId)
      ? selectedProfileId
      : profilesList.length > 0
        ? profilesList[0].profile_id
        : null;

  const selectedProfile = profilesList.find((p) => p.profile_id === activeProfileId) ?? null;

  const profileCount =
    backend.profiles.status === "success" ? backend.profiles.value.length : undefined;

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="BackInTime home">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span>
            <strong>BackInTime</strong>
            <small>Snapshot manager</small>
          </span>
        </a>
        <p className="nav-label">WORKSPACE</p>
        <nav aria-label="Main navigation">
          <NavLink to="/" end className={({ isActive }) => navClass(isActive)}>
            <Icon name="home" className="nav-icon" />
            Overview
          </NavLink>
          <NavLink to="/profiles" className={({ isActive }) => navClass(isActive)}>
            <Icon name="profiles" className="nav-icon" />
            Profiles
            {profileCount !== undefined && <span className="nav-count">{profileCount}</span>}
          </NavLink>
          <span className="nav-item disabled" aria-disabled="true">
            <Icon name="clock" className="nav-icon" />
            Snapshots
            <span className="soon-label">Soon</span>
          </span>
          <span className="nav-item disabled" aria-disabled="true">
            <Icon name="settings" className="nav-icon" />
            Settings
          </span>
        </nav>
        <div className="sidebar-foot">
          <span className={`status-dot ${backend.health.status === "success" ? "online" : ""}`} />
          <span>Local service</span>
          <span className="service-state">
            {backend.health.status === "success"
              ? "Connected"
              : backend.health.status === "loading"
                ? "Connecting"
                : "Offline"}
          </span>
        </div>
      </aside>

      <main id="main-content" className="main-area" tabIndex={-1}>
        <header className="topbar">
          <div className="breadcrumb">BackInTime <span>/</span> Local workspace</div>
          <div className="header-selection" aria-label="Current profile selection">
            <span className="header-selection-label">Selected profile:</span>
            <span
              className={`header-selection-value ${selectedProfile ? "active" : "fallback"}`}
              title={selectedProfile ? selectedProfile.name : "No profile selected"}
            >
              {selectedProfile ? selectedProfile.name : "No profile selected"}
            </span>
          </div>
          <div className="topbar-right">
            <span className="local-badge"><Icon name="device" /> This device</span>
            <span className="avatar" aria-label="Local user">L</span>
          </div>
        </header>
        <Routes>
          <Route
            path="/"
            element={
              <Dashboard
                health={backend.health}
                system={backend.system}
                profiles={backend.profiles}
                selectedProfileId={activeProfileId}
                onSelectProfile={setSelectedProfileId}
                refreshing={backend.refreshing}
                reload={backend.reload}
              />
            }
          />
          <Route
            path="/profiles"
            element={
              <Profiles
                state={backend.profiles}
                selectedProfileId={activeProfileId}
                onSelectProfile={setSelectedProfileId}
                refreshing={backend.refreshing.profiles}
                onReload={backend.reload.profiles}
              />
            }
          />
          <Route path="*" element={<NotFound />} />
        </Routes>
        <footer className="footer-note">
          <span>Independent open-source project</span>
          <span>Local data stays on this device</span>
        </footer>
      </main>
    </div>
  );
}

function navClass(isActive: boolean) {
  return `nav-item${isActive ? " active" : ""}`;
}

type Backend = ReturnType<typeof useBackend>;

function Dashboard({
  health,
  system,
  profiles,
  selectedProfileId,
  onSelectProfile,
  refreshing,
  reload,
}: {
  health: LoadState<Health>;
  system: LoadState<SystemInfo>;
  profiles: LoadState<Profile[]>;
  selectedProfileId?: string | null;
  onSelectProfile?: (id: string) => void;
  refreshing: Backend["refreshing"];
  reload: Backend["reload"];
}) {
  return (
    <section className="page-content">
      <div className="page-heading">
        <div>
          <p className="eyebrow">YOUR BACKUP WORKSPACE</p>
          <h1>Overview</h1>
          <p className="subtitle">A clear view of your backup setup and local service.</p>
        </div>
        <RefreshButton
          label="Refresh overview"
          refreshing={refreshing.any}
          onRefresh={reload.all}
        />
      </div>

      {health.status === "error" && (
        <ErrorBanner subject="service status" state={health} retrying={refreshing.health} onRetry={reload.health} />
      )}
      {profiles.status === "error" && (
        <ErrorBanner subject="profiles" state={profiles} retrying={refreshing.profiles} onRetry={reload.profiles} />
      )}

      <section className="service-banner" aria-label="Backup service status">
        <div className={`service-symbol ${health.status === "success" ? "ready" : ""}`}>
          <Icon name={health.status === "success" ? "check" : "alert"} />
        </div>
        <div className="service-copy">
          <span className="eyebrow">LOCAL BACKUP SERVICE</span>
          <h2>
            {health.status === "loading"
              ? "Checking local service…"
              : health.status === "success"
                ? "Service is ready"
                : "Service is unavailable"}
          </h2>
          <p>
            {health.status === "success"
              ? "The local API is reachable. Backup operations are not available yet."
              : health.status === "error"
                ? "Start the local API, then select Retry to reconnect."
                : "Connecting to the local API."}
          </p>
        </div>
        <div className="service-meta">
          {health.status === "success" ? (
            <>
              <span className="live-indicator" />
              API v{health.value.version}
            </>
          ) : (
            <span className="muted">Loopback only</span>
          )}
        </div>
      </section>

      <div className="stats-grid">
        <StatCard
          label="BACKUP PROFILES"
          value={
            profiles.status === "success"
              ? String(profiles.value.length).padStart(2, "0")
              : profiles.status === "loading"
                ? "—"
                : "!"
          }
          note={profiles.status === "success" ? "Configured on this device" : "Profile data unavailable"}
          icon="profiles"
          accent="blue"
        />
        <StatCard label="SNAPSHOTS" value="—" note="Available after backup engine setup" icon="clock" accent="violet" />
        <StatCard label="LAST BACKUP" value="—" note="No backup operations are available yet" icon="refresh" accent="green" />
      </div>

      <div className="content-grid">
        <section className="panel profile-panel">
          <div className="panel-heading">
            <div>
              <h2>Backup profiles</h2>
              <p>Configured source and destination pairs</p>
            </div>
            <NavLink className="text-link" to="/profiles">View profiles <Icon name="arrowRight" /></NavLink>
          </div>
          <ProfileList
            state={profiles}
            compact
            selectedProfileId={selectedProfileId}
            onSelectProfile={onSelectProfile}
          />
        </section>

        <section className="panel readiness-panel">
          <div className="panel-heading">
            <div>
              <h2>System readiness</h2>
              <p>Runtime checks from this device</p>
            </div>
            <span className="check-icon"><Icon name="check" /></span>
          </div>
          {system.status === "loading" ? (
            <p className="empty-copy">Checking system…</p>
          ) : system.status === "error" ? (
            <ErrorBanner subject="system information" state={system} retrying={refreshing.system} onRetry={reload.system} />
          ) : (
            <dl className="readiness-list">
              <div><dt>Operating system</dt><dd>{system.value.platform}</dd></div>
              <div><dt>Python runtime</dt><dd>{system.value.python_version}</dd></div>
              <div>
                <dt>rsync</dt>
                <dd className={system.value.rsync_available ? "positive-text" : "warning-text"}>
                  <span className="tiny-status" />
                  {system.value.rsync_available ? "Available" : "Not installed"}
                </dd>
              </div>
              <div><dt>Backup engine</dt><dd className="muted">Not implemented</dd></div>
            </dl>
          )}
        </section>
      </div>

      <div className="notice">
        <Icon name="info" className="notice-icon" />
        <p>
          <strong>Early development.</strong> Profile configuration and system checks are
          available. Backup, restore, snapshot browsing, and retention are still under
          development. No backup has been run by this application.
        </p>
      </div>
    </section>
  );
}

function StatCard({
  label,
  value,
  note,
  icon,
  accent,
}: {
  label: string;
  value: string;
  note: string;
  icon: IconName;
  accent: string;
}) {
  return (
    <article className="stat-card">
      <div className={`stat-icon ${accent}`}><Icon name={icon} /></div>
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      <span className="stat-note">{note}</span>
    </article>
  );
}

function Profiles({
  state,
  selectedProfileId,
  onSelectProfile,
  refreshing,
  onReload,
}: {
  state: LoadState<Profile[]>;
  selectedProfileId?: string | null;
  onSelectProfile?: (id: string) => void;
  refreshing: boolean;
  onReload: Reload;
}) {
  return (
    <section className="page-content">
      <div className="page-heading">
        <div>
          <p className="eyebrow">WORKSPACE</p>
          <h1>Profiles</h1>
          <p className="subtitle">Source and destination settings loaded from the local configuration.</p>
        </div>
        <RefreshButton label="Refresh profiles" refreshing={refreshing} onRefresh={onReload} />
      </div>
      {state.status === "error" ? (
        <ErrorBanner subject="profiles" state={state} retrying={refreshing} onRetry={onReload} />
      ) : (
        <section className="panel full-panel">
          <div className="panel-heading">
            <div>
              <h2>Configured profiles</h2>
              <p>Read-only view · editing is not implemented</p>
            </div>
            {state.status === "success" && <span className="result-count">{state.value.length} total</span>}
          </div>
          <ProfileList
            state={state}
            selectedProfileId={selectedProfileId}
            onSelectProfile={onSelectProfile}
          />
        </section>
      )}
      <div className="notice">
        <Icon name="info" className="notice-icon" />
        <p>Profiles can currently be configured by editing the validated local JSON configuration. In-app editing is not available.</p>
      </div>
    </section>
  );
}

function EmptyProfiles() {
  return (
    <div className="empty-state" role="status">
      <span className="empty-icon"><Icon name="profiles" /></span>
      <h3>No profiles configured</h3>
      <p className="empty-explanation">
        No backup profiles were found in your local configuration. In-app profile creation is not currently supported.
      </p>
      <p className="empty-instructions">
        Configure profiles manually in your local configuration file.
      </p>
      <a
        href={CONFIGURATION_DOCS_URL}
        target="_blank"
        rel="noreferrer"
        className="empty-link text-link"
        aria-label="View configuration instructions in README"
      >
        View configuration instructions in README <Icon name="arrowRight" />
      </a>
    </div>
  );
}

function CopyButton({
  text,
  label,
}: {
  text: string;
  label: string;
}) {
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

function ProfileList({
  state,
  compact = false,
  selectedProfileId,
  onSelectProfile,
}: {
  state: LoadState<Profile[]>;
  compact?: boolean;
  selectedProfileId?: string | null;
  onSelectProfile?: (id: string) => void;
}) {
  if (state.status === "loading") return <p className="empty-copy">Loading configured profiles…</p>;
  if (state.status === "error") return <p className="empty-copy">Profile information could not be loaded.</p>;
  if (state.value.length === 0) {
    return <EmptyProfiles />;
  }

  const items = compact ? state.value.slice(0, 3) : state.value;
  return (
    <div className="profile-list" role="list" aria-label="Configured backup profiles">
      {items.map((profile) => {
        const isSelected = profile.profile_id === selectedProfileId;
        return (
          <article
            className={`profile-row ${isSelected ? "selected" : ""}`}
            key={profile.profile_id}
            aria-selected={isSelected}
          >
            <div className="profile-header-row">
              <span className="profile-avatar" aria-hidden="true">
                {profile.name.trim().charAt(0).toUpperCase()}
              </span>
              <div className="profile-detail">
                <strong title={profile.name}>{profile.name}</strong>
                <span>
                  {profile.sources.length} source{profile.sources.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="profile-header-actions">
                {onSelectProfile && (
                  <button
                    type="button"
                    className={`select-profile-btn ${isSelected ? "selected" : ""}`}
                    onClick={() => onSelectProfile(profile.profile_id)}
                    aria-pressed={isSelected}
                    aria-label={`Select profile ${profile.name}`}
                  >
                    {isSelected ? "Selected" : "Select"}
                  </button>
                )}
                <span className="configured-badge"><span /> Configured</span>
              </div>
            </div>

            <div className="profile-paths-container">
              <div className="path-group">
                <span className="path-group-label">SOURCES</span>
                {profile.sources.length === 0 ? (
                  <span className="path-empty">No source paths configured</span>
                ) : (
                  profile.sources.map((src, idx) => (
                    <div key={idx} className="path-row">
                      <code
                        className="path-value"
                        title={src}
                        tabIndex={0}
                        aria-label={`Source path: ${src}`}
                      >
                        {src}
                      </code>
                      <CopyButton text={src} label="Copy source path" />
                    </div>
                  ))
                )}
              </div>

              <div className="path-group">
                <span className="path-group-label">DESTINATION</span>
                <div className="path-row">
                  <code
                    className="path-value"
                    title={profile.target}
                    tabIndex={0}
                    aria-label={`Destination path: ${profile.target}`}
                  >
                    {profile.target}
                  </code>
                  <CopyButton text={profile.target} label="Copy destination path" />
                </div>
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}

function ErrorBanner({
  subject,
  state,
  retrying = false,
  onRetry,
}: {
  subject: string;
  state: Extract<LoadState<unknown>, { status: "error" }>;
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

function RefreshButton({
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

function NotFound() {
  return (
    <section className="page-content">
      <div className="page-heading">
        <div><p className="eyebrow">404</p><h1>Page not found</h1><p className="subtitle">This section is not available.</p></div>
      </div>
      <NavLink className="text-link" to="/">Return to overview <Icon name="arrowRight" /></NavLink>
    </section>
  );
}

export default App;
