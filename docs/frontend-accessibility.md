# Frontend status and contrast

This page records how the web interface communicates status and how its colors measure
against [WCAG 2.2 AA](https://www.w3.org/TR/WCAG22/) in the light theme, which is the only
theme today.

## Status is never color-only

| Indicator | Color cue | Non-color cue |
| --- | --- | --- |
| Local service (sidebar) | Dot | Text: "Connected", "Connecting", or "Offline" |
| Service banner | Green or amber tile | ✓ or ! symbol and heading "Service is ready" or "Service is unavailable" |
| System readiness header | Green, amber, or red tile | ✓ or ! symbol with an accessible label: "System checks passed", "Needs attention: rsync is not installed", or "System checks unavailable" |
| rsync row | Dot and text color | Text: "Available" or "Not installed" |
| Profile badge | Dot | Text: "Configured" |
| Copy button | Green or red | Text: "Copied" or "Failed", announced through a live region |
| Error banners | Red | ! symbol, "Could not load data" heading, `role="alert"` |

The readiness header previously showed a green ✓ even when rsync was missing or the
system check failed. It now reflects the actual state.

## Contrast targets

- Text: at least 4.5:1, because all status text is under 18px.
- Status dots, symbol tiles, and focus outlines: at least 3:1 against their background.

`src/styles.contrast.test.ts` reads the real colors from `src/styles.css` and checks every
status and error pair above. It fails if a later change drops a pair below its target.
Status colors were darkened only as much as needed to pass, keeping the same hue.

| Style | Before | After |
| --- | --- | --- |
| Offline service state text | 2.74:1 | 4.67:1 |
| Muted status text ("Loopback only", "Not implemented") | 2.40:1 | 4.74:1 |
| Positive text ("Available") | 3.67:1 | 4.71:1 |
| Warning text ("Not installed") | 4.01:1 | 4.66:1 |
| Configured badge | 3.84:1 | 4.72:1 |
| Error banner message | 3.98:1 | 4.68:1 |
| Copy "Copied" state | 3.19:1 | 4.75:1 |
| Offline / online / warning dots | 1.68 / 2.50 / 2.26:1 | 3.19 / 3.18 / 3.19:1 |

## Remaining exceptions

Secondary, non-status text still uses light gray and is below 4.5:1. Changing it affects
the overall visual design, so it is left for a separate change:

| Style | Example | Contrast |
| --- | --- | --- |
| `.eyebrow`, `.nav-label` | "YOUR BACKUP WORKSPACE" | 2.55:1 |
| `.subtitle` | Page subtitles | 3.00:1 |
| `.stat-label`, `.stat-note` | Stat card labels and notes | 2.92:1, 2.80:1 |
| `.panel-heading p` | Panel descriptions | 2.80:1 |
| `.empty-copy` | "Loading configured profiles…" | 2.74:1 |
| `.notice p` | Early-development notice | 3.50:1 |
| `.breadcrumb`, `.result-count`, `.readiness-list dt` | Header path, counts, row labels | 3.11 to 3.63:1 |
| `.footer-note` | Footer | 2.22:1 |

A dark theme is not implemented, so no dark-mode contrast is claimed.
