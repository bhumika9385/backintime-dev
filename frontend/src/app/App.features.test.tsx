import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { BackupJob, Profile } from "../types/api";

const home: Profile = {
  profile_id: "home",
  name: "Home files",
  sources: ["C:\\Users\\Example\\Documents"],
  target: "D:\\Backups",
  excludes: [],
};

interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

function json(payload: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(status === 204 ? null : JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** A small in-memory backend that implements the profile revision protocol. */
function fakeBackend(initial: Profile[] = [home]) {
  let profiles = [...initial];
  let revision = 1;
  const calls: Call[] = [];
  let failNextWrite: number | null = null;
  const snapshots = [
    { snapshot_id: "20260301-120000-001", name: null, healthy: false },
    { snapshot_id: "20260201-090000-001", name: null, healthy: true },
  ];
  // Each job advances one state per status poll: queued -> running -> completed.
  const jobs: BackupJob[] = [];
  const advance = (job: BackupJob) => {
    if (job.state === "queued") Object.assign(job, { state: "running", files_copied: 3, bytes_copied: 4096 });
    else if (job.state === "running") {
      Object.assign(job, { state: "completed", files_copied: 7, bytes_copied: 3 * 1024 ** 2, snapshot_id: "20260401-080000-000" });
      snapshots.unshift({ snapshot_id: "20260401-080000-000", name: null, healthy: true });
    }
  };

  const handler = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    const headers = (init.headers ?? {}) as Record<string, string>;
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, headers, body });
    const etag = `"rev-${revision}"`;

    if (url === "/api/v1/health") return json({ status: "ok", version: "0.1.0" });
    if (url === "/api/v1/system/info") {
      return json({ platform: "Windows", python_version: "3.12.7", rsync_available: false });
    }
    if (url === "/api/v1/profiles" && method === "GET") return json({ profiles }, 200, { ETag: etag });
    if (url.endsWith("/snapshots")) return json({ profile_id: "home", snapshots: [...snapshots] });
    if (url === "/api/v1/jobs") {
      jobs.forEach(advance);
      return json({ jobs: jobs.map((job) => ({ ...job })) });
    }
    if (url.endsWith("/backups") && method === "POST") {
      if (jobs.some((job) => job.state === "queued" || job.state === "running")) {
        return json({ error: { code: "JOB_CONFLICT", message: "This profile already has a queued or running backup." } }, 409);
      }
      const job: BackupJob = {
        job_id: `job-${jobs.length + 1}`,
        profile_id: "home",
        state: "queued",
        created_at: "2026-04-01T08:00:00+00:00",
        updated_at: "2026-04-01T08:00:00+00:00",
        files_copied: 0,
        bytes_copied: 0,
        snapshot_id: null,
        error_message: null,
        logs: [],
      };
      jobs.push(job);
      return json(job, 202);
    }
    if (url.endsWith("/cancel") && method === "POST") {
      const job = jobs.find((item) => url.includes(item.job_id))!;
      job.state = "cancelled";
      return json(job);
    }
    if (url.includes("/files?")) {
      const path = new URL(url, "http://localhost").searchParams.get("path");
      return json({
        snapshot_id: "20260201-090000-001",
        path,
        entries:
          path === ""
            ? [
                { name: "Documents", kind: "directory", size_bytes: 0, modified_time_ns: 1.7e18 },
                { name: "notes.txt", kind: "file", size_bytes: 2048, modified_time_ns: 1.7e18 },
              ]
            : [{ name: "report.pdf", kind: "file", size_bytes: 5 * 1024 ** 2, modified_time_ns: 1.7e18 }],
        next_cursor: null,
      });
    }

    if (failNextWrite) {
      const status = failNextWrite;
      failNextWrite = null;
      revision += 1;
      return json({ error: { code: "CONFIGURATION_CHANGED", message: "Configuration changed." } }, status);
    }
    if (headers["If-Match"] !== etag) {
      return json({ error: { code: "CONFIGURATION_CHANGED", message: "Configuration changed." } }, 412);
    }
    revision += 1;
    const newEtag = `"rev-${revision}"`;
    if (url === "/api/v1/profiles" && method === "POST") {
      profiles = [...profiles, body as Profile];
      return json(body, 201, { ETag: newEtag });
    }
    const id = decodeURIComponent(url.split("/").pop() ?? "");
    if (method === "PATCH") {
      profiles = profiles.map((p) => (p.profile_id === id ? { ...p, ...(body as object) } : p));
      return json(profiles.find((p) => p.profile_id === id), 200, { ETag: newEtag });
    }
    if (method === "DELETE") {
      profiles = profiles.filter((p) => p.profile_id !== id);
      return json(null, 204, { ETag: newEtag });
    }
    return json({ error: { code: "NOT_FOUND", message: "Not found" } }, 404);
  });

  vi.stubGlobal("fetch", handler);
  return {
    calls,
    writes: () => calls.filter((call) => call.method !== "GET"),
    failNextWriteWith: (status: number) => {
      failNextWrite = status;
    },
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("profile management", () => {
  afterEach(cleanup);

  it("creates a profile with the loaded revision and shows it in the list", async () => {
    const backend = fakeBackend([]);
    renderAt("/profiles");

    fireEvent.click(await screen.findByRole("button", { name: /Create your first profile/ }));
    const dialog = screen.getByRole("dialog", { name: "New backup profile" });

    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Work Projects" } });
    expect(within(dialog).getByLabelText("Profile ID")).toHaveValue("work-projects");
    fireEvent.change(within(dialog).getByLabelText("Folders to back up"), {
      target: { value: "C:\\Work\n\n  C:\\Shared  " },
    });
    fireEvent.change(within(dialog).getByLabelText("Snapshot destination"), {
      target: { value: "E:\\Backups\\work" },
    });
    fireEvent.change(within(dialog).getByLabelText(/Exclude rules/), { target: { value: "*.tmp" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create profile" }));

    expect(await screen.findByText("Created Work Projects.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(backend.writes()).toEqual([
      expect.objectContaining({
        method: "POST",
        url: "/api/v1/profiles",
        headers: expect.objectContaining({ "If-Match": '"rev-1"' }),
        body: {
          profile_id: "work-projects",
          name: "Work Projects",
          sources: ["C:\\Work", "C:\\Shared"],
          target: "E:\\Backups\\work",
          excludes: ["*.tmp"],
        },
      }),
    ]);
    expect(within(screen.getByRole("list", { name: "Configured backup profiles" })).getByText("Work Projects"))
      .toBeInTheDocument();
  });

  it("validates required fields and relative paths before saving", async () => {
    const backend = fakeBackend([home]);
    renderAt("/profiles");

    fireEvent.click(await screen.findByRole("button", { name: /New profile/ }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Home files" } });
    fireEvent.change(within(dialog).getByLabelText("Profile ID"), { target: { value: "home" } });
    fireEvent.change(within(dialog).getByLabelText("Folders to back up"), { target: { value: "Documents" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create profile" }));

    expect(within(dialog).getByText("Another profile already uses this ID.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Use a full path.*Documents/)).toBeInTheDocument();
    expect(within(dialog).getByText("Enter the folder where snapshots will be stored.")).toBeInTheDocument();
    expect(backend.writes()).toHaveLength(0);
  });

  it("edits a profile without changing its ID", async () => {
    const backend = fakeBackend([home]);
    renderAt("/profiles");

    fireEvent.click(await screen.findByRole("button", { name: "Edit profile Home files" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Home files" });
    expect(within(dialog).getByLabelText("Profile ID")).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Family files" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Saved Family files.")).toBeInTheDocument();
    expect(backend.writes()[0]).toMatchObject({
      method: "PATCH",
      url: "/api/v1/profiles/home",
      body: { name: "Family files", sources: home.sources, target: home.target, excludes: [] },
    });
  });

  it("reloads and explains when the configuration changed elsewhere", async () => {
    const backend = fakeBackend([home]);
    renderAt("/profiles");

    fireEvent.click(await screen.findByRole("button", { name: "Edit profile Home files" }));
    backend.failNextWriteWith(412);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      /changed somewhere else.*latest version has been loaded/,
    );
    const profileLoads = backend.calls.filter((c) => c.method === "GET" && c.url === "/api/v1/profiles");
    expect(profileLoads.length).toBeGreaterThanOrEqual(2);

    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Saved Home files.")).toBeInTheDocument();
    expect(backend.writes().at(-1)?.headers["If-Match"]).toBe('"rev-2"');
  });

  it("confirms before removing a profile and says snapshots are kept", async () => {
    const backend = fakeBackend([home]);
    renderAt("/profiles");

    fireEvent.click(await screen.findByRole("button", { name: "Remove profile Home files" }));
    const dialog = screen.getByRole("dialog", { name: "Remove Home files?" });
    expect(dialog).toHaveTextContent("not deleted");
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove profile" }));

    expect(await screen.findByText("Removed Home files.")).toBeInTheDocument();
    expect(backend.writes()[0]).toMatchObject({ method: "DELETE", url: "/api/v1/profiles/home" });
    expect(await screen.findByText("No profiles configured")).toBeInTheDocument();
  });

  it("closes the form with Escape without saving", async () => {
    const backend = fakeBackend([home]);
    renderAt("/profiles");

    fireEvent.click(await screen.findByRole("button", { name: /New profile/ }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(backend.writes()).toHaveLength(0);
  });
});

describe("backups and snapshots", () => {
  afterEach(cleanup);

  it("runs a backup, follows its progress, and lists the new snapshot", async () => {
    const backend = fakeBackend([home]);
    renderAt("/snapshots");

    expect(await screen.findByText("No backup has been run for this profile yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Back up now/ }));

    const panel = screen.getByRole("region", { name: "Backup" });
    expect(await within(panel).findByText("Running", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(within(panel).getByRole("progressbar")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Cancel backup" })).toBeInTheDocument();

    expect(await within(panel).findByText("Completed", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(within(panel).getByText("3.0 MB")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Browse files in snapshot 20260401-080000-000" }),
    ).toBeEnabled();
    expect(backend.writes()).toEqual([
      expect.objectContaining({ method: "POST", url: "/api/v1/profiles/home/backups" }),
    ]);
  });

  it("cancels a running backup", async () => {
    const backend = fakeBackend([home]);
    renderAt("/snapshots");

    fireEvent.click(await screen.findByRole("button", { name: /Back up now/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel backup" }));

    const panel = screen.getByRole("region", { name: "Backup" });
    expect(await within(panel).findByText("Cancelled")).toBeInTheDocument();
    expect(within(panel).getByText(/partial snapshot is marked incomplete/)).toBeInTheDocument();
    expect(backend.writes().at(-1)).toMatchObject({ method: "POST", url: "/api/v1/jobs/job-1/cancel" });
  });

  it("lists snapshots, blocks incomplete ones, and browses folders", async () => {
    fakeBackend([home]);
    renderAt("/snapshots");

    const incomplete = await screen.findByRole("button", {
      name: "Browse files in snapshot 20260301-120000-001",
    });
    expect(incomplete).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Browse files in snapshot 20260201-090000-001" }));
    expect(await screen.findByText("notes.txt")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Documents/ }));
    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Folder path" });
    expect(within(crumbs).getByRole("button", { name: "Documents" })).toHaveAttribute("aria-current", "page");

    fireEvent.click(within(crumbs).getByRole("button", { name: /Snapshot root/ }));
    await waitFor(() => expect(screen.getByText("notes.txt")).toBeInTheDocument());
  });
});
