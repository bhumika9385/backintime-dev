import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { MemoryRouter } from "react-router-dom";

const profileResponse = {
  profiles: [
    {
      profile_id: "home",
      name: "Home files",
      sources: ["C:\\Users\\Example\\Documents"],
      target: "D:\\Backups",
      excludes: [],
    },
  ],
};

function mockApi(profiles: typeof profileResponse = profileResponse) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload =
        url.endsWith("/health")
          ? { status: "ok", version: "0.1.0" }
          : url.endsWith("/system/info")
            ? { platform: "Windows", python_version: "3.12.7", rsync_available: false }
            : profiles;
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

describe("BackInTime app", () => {
  beforeEach(() => mockApi());
  afterEach(cleanup);

  it("renders real API status, configured profile and honest feature status", async () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Service is ready")).toBeInTheDocument();
    expect(screen.getAllByText("Home files").length).toBeGreaterThan(0);
    expect(screen.getByText(/No backup has been run by this application/)).toBeInTheDocument();
    expect(screen.getByText("Not installed")).toBeInTheDocument();
  });

  it("shows an actionable message when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Service is unavailable")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(2));
    expect(screen.getAllByText("Could not reach the local BackInTime API.").length).toBeGreaterThan(0);
  });

  it("navigates to the profiles view", async () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    fireEvent.click(
      within(screen.getByRole("navigation")).getByRole("link", { name: /Profiles/ }),
    );

    expect(await screen.findByText("Configured profiles")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("Home files").length).toBeGreaterThan(0));
  });

  it("displays the selected profile in the header and allows switching selection (#29)", async () => {
    const multiProfileResponse = {
      profiles: [
        {
          profile_id: "p1",
          name: "Documents Profile",
          sources: ["/home/user/Documents"],
          target: "/media/backup/docs",
          excludes: [],
        },
        {
          profile_id: "p2",
          name: "Photos Profile",
          sources: ["/home/user/Pictures"],
          target: "/media/backup/photos",
          excludes: [],
        },
      ],
    };

    mockApi(multiProfileResponse);

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    // Initial selection should default to the first profile
    const docProfiles = await screen.findAllByText("Documents Profile");
    expect(docProfiles.length).toBeGreaterThan(0);
    const headerSelection = screen.getByLabelText("Current profile selection");
    expect(within(headerSelection).getByText("Documents Profile")).toBeInTheDocument();

    // Select the second profile
    const selectButtons = screen.getAllByRole("button", { name: /Select profile/ });
    expect(selectButtons.length).toBe(2);
    fireEvent.click(selectButtons[1]);

    // Header updates to the newly selected profile
    expect(within(headerSelection).getByText("Photos Profile")).toBeInTheDocument();
  });

  it("copies source and target paths with feedback (#24)", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    const homeProfiles = await screen.findAllByText("Home files");
    expect(homeProfiles.length).toBeGreaterThan(0);

    const copySrcBtn = screen.getByRole("button", { name: "Copy source path" });
    fireEvent.click(copySrcBtn);

    expect(writeTextMock).toHaveBeenCalledWith("C:\\Users\\Example\\Documents");
    expect(await screen.findByText("Copied")).toBeInTheDocument();

    const copyDestBtn = screen.getByRole("button", { name: "Copy destination path" });
    fireEvent.click(copyDestBtn);
    expect(writeTextMock).toHaveBeenCalledWith("D:\\Backups");
  });

  it("renders accessible long paths with title tooltips (#23, #25)", async () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    const homeProfiles = await screen.findAllByText("Home files");
    expect(homeProfiles.length).toBeGreaterThan(0);

    const srcCode = screen.getByLabelText("Source path: C:\\Users\\Example\\Documents");
    expect(srcCode).toHaveAttribute("title", "C:\\Users\\Example\\Documents");
    expect(srcCode).toHaveAttribute("tabIndex", "0");

    const destCode = screen.getByLabelText("Destination path: D:\\Backups");
    expect(destCode).toHaveAttribute("title", "D:\\Backups");
    expect(destCode).toHaveAttribute("tabIndex", "0");

    // Skip link exists and points to main content
    const skipLink = screen.getByText("Skip to main content");
    expect(skipLink).toBeInTheDocument();
    expect(skipLink).toHaveAttribute("href", "#main-content");
  });

  it("displays helpful empty-profile state when no profiles are configured (#28)", async () => {
    mockApi({ profiles: [] });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("No profiles configured")).toBeInTheDocument();
    expect(
      screen.getByText(/No backup profiles were found in your local configuration/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /View configuration instructions in README/ }),
    ).toBeInTheDocument();

    const headerSelection = screen.getByLabelText("Current profile selection");
    expect(within(headerSelection).getByText("No profile selected")).toBeInTheDocument();
  });

  it("uses decorative local SVG icons instead of text symbols (#27)", async () => {
    const { container } = render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Service is ready")).toBeInTheDocument();
    const icons = container.querySelectorAll("svg.icon");
    expect(icons.length).toBeGreaterThan(0);
    icons.forEach((icon) => {
      expect(icon).toHaveAttribute("aria-hidden", "true");
      expect(icon).toHaveAttribute("focusable", "false");
    });
    expect(container.textContent).not.toMatch(/[⌂▤◷⚙⌑✓↻→⧉]/u);
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Profiles/ })).toBeInTheDocument();
  });

  it("does not show a passing readiness check when rsync is missing (#26)", async () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    const indicator = await screen.findByRole("img", {
      name: "Needs attention: rsync is not installed",
    });
    expect(indicator.querySelector('svg[data-icon="alert"]')).not.toBeNull();
    expect(screen.queryByRole("img", { name: "System checks passed" })).not.toBeInTheDocument();
    expect(screen.getByText("Not installed")).toBeInTheDocument();
  });
});
