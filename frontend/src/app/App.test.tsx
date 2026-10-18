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

function mockApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload =
        url.endsWith("/health")
          ? { status: "ok", version: "0.1.0" }
          : url.endsWith("/system/info")
            ? { platform: "Windows", python_version: "3.12.7", rsync_available: false }
            : profileResponse;
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
    expect(screen.getByText("Home files")).toBeInTheDocument();
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

  it("keeps complete paths available and lets users expand truncated paths", async () => {
    render(
      <MemoryRouter initialEntries={["/profiles"]}>
        <App />
      </MemoryRouter>,
    );

    const toggle = await screen.findByRole("button", { name: "Show full paths for Home files" });
    expect(screen.getByText("C:\\Users\\Example\\Documents")).toHaveAttribute(
      "title",
      "C:\\Users\\Example\\Documents",
    );
    expect(screen.getByText("D:\\Backups")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAccessibleName("Hide full paths for Home files");
    expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).toHaveClass("expanded");
  });
});
