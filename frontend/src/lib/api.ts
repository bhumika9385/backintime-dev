import type {
  ApiErrorResponse,
  BackupJob,
  Health,
  JobList,
  Profile,
  ProfileInput,
  ProfileList,
  ProfilePatch,
  SnapshotDirectory,
  SnapshotList,
  SystemInfo,
} from "../types/api";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly requestId?: string;

  constructor(message: string, status: number, code?: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

/** A response body together with the configuration revision the API reported. */
export interface Revisioned<T> {
  value: T;
  etag: string | null;
}

async function request(path: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, init);
  } catch {
    throw new ApiError("Could not reach the local BackInTime API.", 0);
  }

  if (!response.ok) {
    let body: ApiErrorResponse = {};
    try {
      body = (await response.json()) as ApiErrorResponse;
    } catch {
      // The response may not contain JSON; use the request ID header if present.
    }
    throw new ApiError(
      body.error?.message ?? `The API request failed (${response.status}).`,
      response.status,
      body.error?.code,
      body.error?.request_id ?? response.headers.get("X-Request-ID") ?? undefined,
    );
  }
  return response;
}

async function get<T>(path: string): Promise<T> {
  const response = await request(path, { headers: { Accept: "application/json" } });
  return (await response.json()) as T;
}

async function getWithRevision<T>(path: string): Promise<Revisioned<T>> {
  const response = await request(path, { headers: { Accept: "application/json" } });
  return { value: (await response.json()) as T, etag: response.headers.get("ETag") };
}

async function post<T>(path: string): Promise<T> {
  const response = await request(path, { method: "POST", headers: { Accept: "application/json" } });
  return (await response.json()) as T;
}

/** Send a state-changing request guarded by the configuration revision in `etag`. */
async function write<T>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  etag: string,
  body?: unknown,
): Promise<Revisioned<T | null>> {
  const response = await request(path, {
    method,
    headers: {
      Accept: "application/json",
      "If-Match": etag,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = response.status === 204 ? null : ((await response.json()) as T);
  return { value, etag: response.headers.get("ETag") };
}

const profilePath = (profileId: string) => `/profiles/${encodeURIComponent(profileId)}`;

export const api = {
  health: () => get<Health>("/health"),
  systemInfo: () => get<SystemInfo>("/system/info"),
  profiles: () => get<ProfileList>("/profiles"),
  profilesWithRevision: () => getWithRevision<ProfileList>("/profiles"),
  createProfile: (profile: ProfileInput, etag: string) =>
    write<Profile>("POST", "/profiles", etag, profile),
  updateProfile: (profileId: string, patch: ProfilePatch, etag: string) =>
    write<Profile>("PATCH", profilePath(profileId), etag, patch),
  deleteProfile: (profileId: string, etag: string) =>
    write<never>("DELETE", profilePath(profileId), etag),
  snapshots: (profileId: string) => get<SnapshotList>(`${profilePath(profileId)}/snapshots`),
  startBackup: (profileId: string) => post<BackupJob>(`${profilePath(profileId)}/backups`),
  jobs: () => get<JobList>("/jobs"),
  cancelJob: (jobId: string) => post<BackupJob>(`/jobs/${encodeURIComponent(jobId)}/cancel`),
  snapshotFiles: (profileId: string, snapshotId: string, path: string, after?: string | null) => {
    const query = new URLSearchParams({ path });
    if (after) query.set("after", after);
    return get<SnapshotDirectory>(
      `${profilePath(profileId)}/snapshots/${encodeURIComponent(snapshotId)}/files?${query}`,
    );
  },
};
