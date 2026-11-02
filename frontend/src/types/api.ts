export interface Health {
  status: string;
  version: string;
}

export interface SystemInfo {
  platform: string;
  python_version: string;
  rsync_available: boolean;
}

export interface Profile {
  profile_id: string;
  name: string;
  sources: string[];
  target: string;
  excludes: string[];
}

export interface ProfileList {
  profiles: Profile[];
}

export type ProfileInput = Profile;

export type ProfilePatch = Partial<Omit<Profile, "profile_id">>;

export interface SnapshotSummary {
  snapshot_id: string;
  name: string | null;
  healthy: boolean;
}

export interface SnapshotList {
  profile_id: string;
  snapshots: SnapshotSummary[];
}

export interface SnapshotEntry {
  name: string;
  kind: "directory" | "file" | "symlink" | "other";
  size_bytes: number;
  modified_time_ns: number;
}

export interface SnapshotDirectory {
  snapshot_id: string;
  path: string;
  entries: SnapshotEntry[];
  next_cursor: string | null;
}

export type JobState = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface BackupJob {
  job_id: string;
  profile_id: string;
  state: JobState;
  created_at: string;
  updated_at: string;
  files_copied: number;
  bytes_copied: number;
  snapshot_id: string | null;
  error_message: string | null;
  logs: { timestamp: string; message: string }[];
}

export interface JobList {
  jobs: BackupJob[];
}

export interface ApiErrorResponse {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
  };
  detail?: unknown;
}
