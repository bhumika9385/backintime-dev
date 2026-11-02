import type { ReactNode } from "react";

/**
 * Local inline SVG icons drawn on a shared 24px grid with a 1.75 stroke.
 *
 * Icons inherit color from `currentColor` and are sized in `em`, so the
 * surrounding element's `font-size` controls them. They are decorative and
 * hidden from assistive technologies; any control that shows only an icon
 * must provide its own accessible name.
 */
const paths = {
  home: (
    <>
      <path d="M4 10.5 12 4l8 6.5" />
      <path d="M6 9v10h12V9" />
      <path d="M10 19v-5h4v5" />
    </>
  ),
  profiles: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="2.5" />
      <path d="M8 9h8M8 12h8M8 15h5" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M6 18l1.6-1.6M16.4 7.6 18 6" />
    </>
  ),
  device: (
    <>
      <rect x="3.5" y="5" width="17" height="11" rx="1.5" />
      <path d="M9 19.5h6M12 16v3.5" />
    </>
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  alert: (
    <>
      <path d="M12 5.5v8" />
      <path d="M12 18.25v.25" />
    </>
  ),
  refresh: (
    <>
      <path d="M19 12a7 7 0 1 1-2.05-4.95" />
      <path d="M19 4.5V9h-4.5" />
    </>
  ),
  arrowRight: <path d="M5 12h14m-5-5 5 5-5 5" />,
  copy: (
    <>
      <rect x="8.5" y="8.5" width="11" height="11" rx="2" />
      <path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5.5" />
      <path d="M12 7.75V8" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  edit: (
    <>
      <path d="M4.5 19.5h4l10-10a2.83 2.83 0 0 0-4-4l-10 10v4Z" />
      <path d="m13.5 6.5 4 4" />
    </>
  ),
  trash: (
    <>
      <path d="M4.5 7h15M9.5 7V4.5h5V7" />
      <path d="M6.5 7l1 12.5h9l1-12.5M10 11v5M14 11v5" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6 6 18" />,
  folder: <path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z" />,
  file: (
    <>
      <path d="M6.5 3.5h7l4 4v13h-11Z" />
      <path d="M13.5 3.5v4h4" />
    </>
  ),
  link: (
    <>
      <path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1" />
      <path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1" />
    </>
  ),
  chevronRight: <path d="m9.5 6 6 6-6 6" />,
  drive: (
    <>
      <rect x="3.5" y="13" width="17" height="6.5" rx="1.5" />
      <path d="M5 13 7.5 5h9L19 13M16.5 16.25h.01" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof paths;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg
      className={className ? `icon ${className}` : "icon"}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      data-icon={name}
    >
      {paths[name]}
    </svg>
  );
}
