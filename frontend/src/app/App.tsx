import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { ApiError, api } from "../lib/api";
import { Icon } from "./icons";
import { DeleteProfileDialog, ProfileForm } from "./ProfileForm";
import {
  CopyButton,
  ErrorBanner,
  RefreshButton,
  useResource,
  type LoadState,
  type Reload,
} from "./shared";
import { SnapshotsPage } from "./SnapshotsPage";
import type { Health, Profile, ProfileInput, SystemInfo } from "../types/api";

const CONFIGURATION_DOCS_URL = "https://github.com/timevault-dev/BackInTime#getting-started";
const STALE_CONFIGURATION_MESSAGE =
  "The configuration was changed somewhere else. The latest version has been loaded; review it and try again.";

function useBackend() {
  // The configuration revision from the latest profile load, sent with every profile change.
  const revision = useRef<string | null>(null);
  const loadProfiles = useCallback(async () => {
    const { value, etag } = await api.profilesWithRevision();
    revision.current = etag;
    return value.profiles;
  }, []);

  const health = useResource<Health>(api.health);
  const system = useResource<SystemInfo>(api.systemInfo);
  const profiles = useResource<Profile[]>(loadProfiles);
  const reloadHealth = health.reload;
  const reloadSystem = system.reload;
  const reloadProfiles = profiles.reload;
  const reloadAll = useCallback(async () => {
    await Promise.all([reloadHealth(), reloadSystem(), reloadProfiles()]);
  }, [reloadHealth, reloadSystem, reloadProfiles]);

  /** Run one profile change against the loaded revision, then reload the profile list. */
  const mutate = useCallback(
    async (change: (etag: string) => Promise<unknown>) => {
      if (!revision.current) {
        await reloadProfiles();
        if (!revision.current) throw new Error("Profiles must load before they can be changed.");
      }
      try {
        await change(revision.current);
      } catch (error) {
        if (error instanceof ApiError && (error.status === 412 || error.status === 428)) {
          await reloadProfiles();
          throw new ApiError(STALE_CONFIGURATION_MESSAGE, error.status, error.code, error.requestId);
        }
        throw error;
      }
      await reloadProfiles();
    },
    [reloadProfiles],
  );

  const profileActions = useMemo(
    () => ({
      create: (input: ProfileInput) => mutate((etag) => api.createProfile(input, etag)),
      update: (profileId: string, input: ProfileInput) =>
        mutate((etag) =>
          api.updateProfile(
            profileId,
            { name: input.name, sources: input.sources, target: input.target, excludes: input.excludes },
            etag,
          ),
        ),
      remove: (profileId: string) => mutate((etag) => api.deleteProfile(profileId, etag)),
    }),
    [mutate],
  );

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
    profileActions,
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
          <NavLink to="/snapshots" className={({ isActive }) => navClass(isActive)}>
            <Icon name="clock" className="nav-icon" />
            Snapshots
          </NavLink>
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
                actions={backend.profileActions}
              />
            }
          />
          <Route
            path="/snapshots"
            element={
              <SnapshotsPage
                profiles={backend.profiles}
                selectedProfileId={activeProfileId}
                onSelectProfile={setSelectedProfileId}
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
              ? "The local API is reachable. You can manage profiles and run backups."
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

      <div className="content-grid">
        <section className="panel profile-panel">
          <div className="panel-heading">
            <div>
              <h2>Backup profiles</h2>
              <p>Configured source and destination pairs</p>
            </div>
            <NavLink className="text-link" to="/profiles">Manage profiles <Icon name="arrowRight" /></NavLink>
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
              <div>
                <dt>Backup engine</dt>
                <dd className="positive-text"><span className="tiny-status" />Built-in copy</dd>
              </div>
            </dl>
          )}
        </section>
      </div>

      <div className="notice">
        <Icon name="info" className="notice-icon" />
        <p>
          <strong>Early development.</strong> Profiles, backups, and read-only snapshot
          browsing are available. Restore and retention are still under development. Test with
          disposable data, not your only copy of important files.
        </p>
      </div>
    </section>
  );
}

type ProfileActions = Backend["profileActions"];

type Editing = { mode: "create" } | { mode: "edit"; profile: Profile } | { mode: "delete"; profile: Profile };

function Profiles({
  state,
  selectedProfileId,
  onSelectProfile,
  refreshing,
  onReload,
  actions,
}: {
  state: LoadState<Profile[]>;
  selectedProfileId?: string | null;
  onSelectProfile?: (id: string) => void;
  refreshing: boolean;
  onReload: Reload;
  actions: ProfileActions;
}) {
  const [editing, setEditing] = useState<Editing | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const existingIds = state.status === "success" ? state.value.map((p) => p.profile_id) : [];

  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), 4000);
    return () => clearTimeout(timer);
  }, [message]);

  const finish = (text: string) => {
    setEditing(null);
    setMessage(text);
  };

  return (
    <section className="page-content">
      <div className="page-heading">
        <div>
          <p className="eyebrow">WORKSPACE</p>
          <h1>Profiles</h1>
          <p className="subtitle">Choose what to back up and where snapshots are stored.</p>
        </div>
        <div className="heading-actions">
          <RefreshButton label="Refresh profiles" refreshing={refreshing} onRefresh={onReload} />
          <button
            type="button"
            className="primary-button"
            onClick={() => setEditing({ mode: "create" })}
            disabled={state.status !== "success"}
          >
            <Icon name="plus" /> New profile
          </button>
        </div>
      </div>
      <div className="toast-region" role="status" aria-live="polite">
        {message && (
          <div className="toast">
            <Icon name="check" /> {message}
          </div>
        )}
      </div>
      {state.status === "error" ? (
        <ErrorBanner subject="profiles" state={state} retrying={refreshing} onRetry={onReload} />
      ) : (
        <section className="panel full-panel">
          <div className="panel-heading">
            <div>
              <h2>Configured profiles</h2>
              <p>Changes are validated and saved to the local configuration file</p>
            </div>
            {state.status === "success" && <span className="result-count">{state.value.length} total</span>}
          </div>
          <ProfileList
            state={state}
            selectedProfileId={selectedProfileId}
            onSelectProfile={onSelectProfile}
            onCreate={() => setEditing({ mode: "create" })}
            onEdit={(profile) => setEditing({ mode: "edit", profile })}
            onDelete={(profile) => setEditing({ mode: "delete", profile })}
          />
        </section>
      )}
      <div className="notice">
        <Icon name="info" className="notice-icon" />
        <p>
          Removing a profile only removes it from the configuration. Snapshots already stored in
          its destination are never deleted.
        </p>
      </div>

      {editing?.mode === "create" && (
        <ProfileForm
          profile={null}
          existingIds={existingIds}
          onCancel={() => setEditing(null)}
          onSubmit={async (input) => {
            await actions.create(input);
            onSelectProfile?.(input.profile_id);
            finish(`Created ${input.name}.`);
          }}
        />
      )}
      {editing?.mode === "edit" && (
        <ProfileForm
          profile={editing.profile}
          existingIds={existingIds}
          onCancel={() => setEditing(null)}
          onSubmit={async (input) => {
            await actions.update(editing.profile.profile_id, input);
            finish(`Saved ${input.name}.`);
          }}
        />
      )}
      {editing?.mode === "delete" && (
        <DeleteProfileDialog
          profile={editing.profile}
          onCancel={() => setEditing(null)}
          onConfirm={async () => {
            await actions.remove(editing.profile.profile_id);
            finish(`Removed ${editing.profile.name}.`);
          }}
        />
      )}
    </section>
  );
}

function EmptyProfiles({ onCreate }: { onCreate?: () => void }) {
  return (
    <div className="empty-state" role="status">
      <span className="empty-icon"><Icon name="profiles" /></span>
      <h3>No profiles configured</h3>
      <p className="empty-explanation">
        No backup profiles were found in your local configuration. A profile says which folders
        to back up and where to store their snapshots.
      </p>
      {onCreate ? (
        <button type="button" className="primary-button" onClick={onCreate}>
          <Icon name="plus" /> Create your first profile
        </button>
      ) : (
        <NavLink className="primary-button" to="/profiles">
          <Icon name="plus" /> Create a profile
        </NavLink>
      )}
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

function ProfileList({
  state,
  compact = false,
  selectedProfileId,
  onSelectProfile,
  onCreate,
  onEdit,
  onDelete,
}: {
  state: LoadState<Profile[]>;
  compact?: boolean;
  selectedProfileId?: string | null;
  onSelectProfile?: (id: string) => void;
  onCreate?: () => void;
  onEdit?: (profile: Profile) => void;
  onDelete?: (profile: Profile) => void;
}) {
  if (state.status === "loading") return <p className="empty-copy">Loading configured profiles…</p>;
  if (state.status === "error") return <p className="empty-copy">Profile information could not be loaded.</p>;
  if (state.value.length === 0) {
    return <EmptyProfiles onCreate={onCreate} />;
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
            role="listitem"
          >
            <div className="profile-header-row">
              <span className="profile-avatar" aria-hidden="true">
                {profile.name.trim().charAt(0).toUpperCase()}
              </span>
              <div className="profile-detail">
                <strong title={profile.name}>{profile.name}</strong>
                <span>
                  {profile.sources.length} source{profile.sources.length === 1 ? "" : "s"}
                  {profile.excludes.length > 0 &&
                    ` · ${profile.excludes.length} exclude rule${profile.excludes.length === 1 ? "" : "s"}`}
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
                {onEdit && (
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() => onEdit(profile)}
                    aria-label={`Edit profile ${profile.name}`}
                    title="Edit profile"
                  >
                    <Icon name="edit" />
                  </button>
                )}
                {onDelete && (
                  <button
                    type="button"
                    className="icon-button danger"
                    onClick={() => onDelete(profile)}
                    aria-label={`Remove profile ${profile.name}`}
                    title="Remove profile"
                  >
                    <Icon name="trash" />
                  </button>
                )}
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
