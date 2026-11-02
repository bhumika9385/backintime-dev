import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";

import { api } from "../lib/api";
import type {
  BackupJob,
  Profile,
  SnapshotEntry,
  SnapshotList,
  SnapshotSummary,
} from "../types/api";
import { Icon, type IconName } from "./icons";
import {
  ErrorBanner,
  RefreshButton,
  StateBadge,
  errorMessage,
  formatBytes,
  formatSnapshotDate,
  useResource,
  type LoadState,
} from "./shared";

export function SnapshotsPage({
  profiles,
  selectedProfileId,
  onSelectProfile,
}: {
  profiles: LoadState<Profile[]>;
  selectedProfileId: string | null;
  onSelectProfile: (id: string) => void;
}) {
  const profileList = profiles.status === "success" ? profiles.value : [];
  const profile = profileList.find((item) => item.profile_id === selectedProfileId) ?? null;

  return (
    <section className="page-content">
      <div className="page-heading">
        <div>
          <p className="eyebrow">BACKUPS</p>
          <h1>Snapshots</h1>
          <p className="subtitle">Back up a profile, then browse the files stored in each snapshot.</p>
        </div>
        {profileList.length > 0 && (
          <label className="profile-picker">
            <span>Profile</span>
            <select
              value={selectedProfileId ?? ""}
              onChange={(event) => onSelectProfile(event.target.value)}
            >
              {profileList.map((item) => (
                <option key={item.profile_id} value={item.profile_id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {profiles.status === "loading" ? (
        <p className="empty-copy">Loading profiles…</p>
      ) : profiles.status === "error" ? (
        <ErrorBanner subject="profiles" state={profiles} />
      ) : profile === null ? (
        <section className="panel full-panel">
          <div className="empty-state" role="status">
            <span className="empty-icon"><Icon name="clock" /></span>
            <h3>No profile to inspect</h3>
            <p>Create a backup profile first. Its snapshots will appear here.</p>
            <NavLink className="text-link" to="/profiles">
              Go to profiles <Icon name="arrowRight" />
            </NavLink>
          </div>
        </section>
      ) : (
        <ProfileSnapshots key={profile.profile_id} profile={profile} />
      )}
    </section>
  );
}

function ProfileSnapshots({ profile }: { profile: Profile }) {
  const loadSnapshots = useCallback(() => api.snapshots(profile.profile_id), [profile.profile_id]);
  const snapshots = useResource<SnapshotList>(loadSnapshots);
  const [browsing, setBrowsing] = useState<SnapshotSummary | null>(null);
  const reloadSnapshots = snapshots.reload;

  return (
    <>
      <div className="snapshot-layout">
        <BackupPanel profile={profile} onFinished={reloadSnapshots} />

        <section className="panel snapshot-list-panel">
          <div className="panel-heading">
            <div>
              <h2>Snapshots</h2>
              <p>Newest first · only complete snapshots can be browsed</p>
            </div>
            <RefreshButton label="Refresh" refreshing={snapshots.refreshing} onRefresh={reloadSnapshots} />
          </div>
          {snapshots.state.status === "loading" ? (
            <p className="empty-copy">Loading snapshots…</p>
          ) : snapshots.state.status === "error" ? (
            <ErrorBanner
              subject="snapshots"
              state={snapshots.state}
              retrying={snapshots.refreshing}
              onRetry={reloadSnapshots}
            />
          ) : snapshots.state.value.snapshots.length === 0 ? (
            <div className="empty-state" role="status">
              <span className="empty-icon"><Icon name="clock" /></span>
              <h3>No snapshots yet</h3>
              <p>Select Back up now to create the first snapshot for this profile.</p>
            </div>
          ) : (
            <ul className="snapshot-list" aria-label="Snapshots">
              {snapshots.state.value.snapshots.map((snapshot) => {
                const active = browsing?.snapshot_id === snapshot.snapshot_id;
                return (
                  <li key={snapshot.snapshot_id} className={`snapshot-row${active ? " active" : ""}`}>
                    <span className={`snapshot-dot${snapshot.healthy ? " healthy" : ""}`} aria-hidden="true" />
                    <div className="snapshot-copy">
                      <strong>{snapshot.name || formatSnapshotDate(snapshot.snapshot_id)}</strong>
                      <code>{snapshot.snapshot_id}</code>
                    </div>
                    <StateBadge state={snapshot.healthy ? "healthy" : "incomplete"} />
                    <button
                      type="button"
                      className="secondary-button small"
                      disabled={!snapshot.healthy}
                      title={snapshot.healthy ? undefined : "Incomplete snapshots cannot be browsed"}
                      aria-pressed={active}
                      aria-label={`Browse files in snapshot ${snapshot.snapshot_id}`}
                      onClick={() => setBrowsing(snapshot)}
                    >
                      Browse
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      {browsing && (
        <SnapshotBrowser
          key={browsing.snapshot_id}
          profileId={profile.profile_id}
          snapshot={browsing}
          onClose={() => setBrowsing(null)}
        />
      )}
    </>
  );
}

const POLL_INTERVAL_MS = 1000;
const isActive = (job: BackupJob) => job.state === "queued" || job.state === "running";

/** Start backups for one profile and follow the latest job until it finishes. */
function BackupPanel({ profile, onFinished }: { profile: Profile; onFinished: () => Promise<void> }) {
  const [job, setJob] = useState<BackupJob | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const wasActive = useRef(false);

  const refreshJob = useCallback(async () => {
    try {
      const { jobs } = await api.jobs();
      const latest =
        jobs
          .filter((item) => item.profile_id === profile.profile_id)
          .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null;
      setJob(latest);
      setLoadError(null);
      const active = latest !== null && isActive(latest);
      if (wasActive.current && !active) void onFinished();
      wasActive.current = active;
    } catch (error) {
      setLoadError(errorMessage(error));
    }
  }, [profile.profile_id, onFinished]);

  useEffect(() => {
    void refreshJob();
  }, [refreshJob]);

  const active = job !== null && isActive(job);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refreshJob(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [active, refreshJob]);

  const start = async () => {
    setStarting(true);
    setActionError(null);
    try {
      const started = await api.startBackup(profile.profile_id);
      setJob(started);
      wasActive.current = true;
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    setCancelling(true);
    setActionError(null);
    try {
      setJob(await api.cancelJob(job.job_id));
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setCancelling(false);
    }
  };

  return (
    <section className="panel backup-panel" aria-label="Backup">
      <div className="panel-heading">
        <div>
          <h2>Backup</h2>
          <p>Copies the profile folders into a new snapshot</p>
        </div>
        {job && <StateBadge state={job.state} />}
      </div>

      <div className="backup-body">
        {active ? (
          <button
            type="button"
            className="secondary-button"
            onClick={() => void cancel()}
            disabled={cancelling}
            aria-busy={cancelling}
          >
            {cancelling ? "Cancelling…" : "Cancel backup"}
          </button>
        ) : (
          <button
            type="button"
            className="primary-button"
            onClick={() => void start()}
            disabled={starting}
            aria-busy={starting}
          >
            <Icon name="refresh" /> {starting ? "Starting…" : "Back up now"}
          </button>
        )}

        {actionError && <p className="form-alert" role="alert">{actionError}</p>}
        {loadError && <p className="field-error" role="alert">Job status unavailable: {loadError}</p>}

        {job ? (
          <div className="job-status" aria-live="polite">
            <dl className="readiness-list">
              <div><dt>Files copied</dt><dd>{job.files_copied.toLocaleString()}</dd></div>
              <div><dt>Data copied</dt><dd>{formatBytes(job.bytes_copied)}</dd></div>
              <div>
                <dt>Started</dt>
                <dd>
                  {new Date(job.created_at).toLocaleString(undefined, {
                    dateStyle: "medium",
                    timeStyle: "short",
                  })}
                </dd>
              </div>
            </dl>
            {active && <div className="job-progress" role="progressbar" aria-label="Backup in progress" />}
            {job.state === "failed" && job.error_message && (
              <p className="form-alert" role="alert">{job.error_message}</p>
            )}
            {job.state === "cancelled" && (
              <p className="panel-note">The backup was cancelled. Its partial snapshot is marked incomplete.</p>
            )}
          </div>
        ) : (
          !loadError && <p className="panel-note">No backup has been run for this profile yet.</p>
        )}
      </div>
    </section>
  );
}

const ENTRY_ICONS: Record<SnapshotEntry["kind"], IconName> = {
  directory: "folder",
  file: "file",
  symlink: "link",
  other: "file",
};

interface DirectoryPage {
  path: string;
  entries: SnapshotEntry[];
  nextCursor: string | null;
}

function SnapshotBrowser({
  profileId,
  snapshot,
  onClose,
}: {
  profileId: string;
  snapshot: SnapshotSummary;
  onClose: () => void;
}) {
  const [path, setPath] = useState("");
  const [page, setPage] = useState<LoadState<DirectoryPage>>({ status: "loading" });
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setPage({ status: "loading" });
    setMoreError(null);
    api
      .snapshotFiles(profileId, snapshot.snapshot_id, path)
      .then((listing) => {
        if (!cancelled) {
          setPage({
            status: "success",
            value: { path: listing.path, entries: listing.entries, nextCursor: listing.next_cursor },
          });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setPage({ status: "error", message: errorMessage(error), failedAt: Date.now() });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [profileId, snapshot.snapshot_id, path, attempt]);

  const loadMore = async () => {
    if (page.status !== "success" || !page.value.nextCursor) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const listing = await api.snapshotFiles(
        profileId,
        snapshot.snapshot_id,
        page.value.path,
        page.value.nextCursor,
      );
      setPage({
        status: "success",
        value: {
          path: page.value.path,
          entries: [...page.value.entries, ...listing.entries],
          nextCursor: listing.next_cursor,
        },
      });
    } catch (error) {
      setMoreError(errorMessage(error));
    } finally {
      setLoadingMore(false);
    }
  };

  const segments = path ? path.split("/") : [];

  return (
    <section className="panel file-browser" aria-label="Snapshot files">
      <div className="panel-heading">
        <div>
          <h2>Files in {snapshot.name || formatSnapshotDate(snapshot.snapshot_id)}</h2>
          <p>Read-only view · nothing here can be changed or deleted</p>
        </div>
        <button type="button" className="icon-button" aria-label="Close file browser" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>

      <nav className="crumbs" aria-label="Folder path">
        <button type="button" onClick={() => setPath("")} aria-current={path === "" ? "page" : undefined}>
          <Icon name="drive" /> Snapshot root
        </button>
        {segments.map((segment, index) => {
          const target = segments.slice(0, index + 1).join("/");
          return (
            <span key={target} className="crumb">
              <Icon name="chevronRight" className="crumb-separator" />
              <button
                type="button"
                onClick={() => setPath(target)}
                aria-current={index === segments.length - 1 ? "page" : undefined}
              >
                {segment}
              </button>
            </span>
          );
        })}
      </nav>

      {page.status === "loading" ? (
        <p className="empty-copy">Loading folder…</p>
      ) : page.status === "error" ? (
        <ErrorBanner
          subject="this folder"
          state={page}
          onRetry={async () => setAttempt((value) => value + 1)}
        />
      ) : page.value.entries.length === 0 ? (
        <p className="empty-copy">This folder is empty.</p>
      ) : (
        <>
          <table className="file-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col" className="numeric">Size</th>
                <th scope="col">Modified</th>
              </tr>
            </thead>
            <tbody>
              {page.value.entries.map((entry) => (
                <tr key={entry.name}>
                  <td>
                    {entry.kind === "directory" ? (
                      <button
                        type="button"
                        className="file-name folder"
                        onClick={() => setPath(path ? `${path}/${entry.name}` : entry.name)}
                      >
                        <Icon name="folder" /> {entry.name}
                      </button>
                    ) : (
                      <span className="file-name" title={entry.kind === "symlink" ? "Symbolic link" : undefined}>
                        <Icon name={ENTRY_ICONS[entry.kind]} /> {entry.name}
                      </span>
                    )}
                  </td>
                  <td className="numeric">{entry.kind === "directory" ? "—" : formatBytes(entry.size_bytes)}</td>
                  <td>{new Date(entry.modified_time_ns / 1e6).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {moreError && <p className="field-error" role="alert">{moreError}</p>}
          {page.value.nextCursor && (
            <button
              type="button"
              className="secondary-button load-more"
              onClick={() => void loadMore()}
              disabled={loadingMore}
              aria-busy={loadingMore}
            >
              {loadingMore ? "Loading…" : "Load more"}
            </button>
          )}
        </>
      )}
    </section>
  );
}
