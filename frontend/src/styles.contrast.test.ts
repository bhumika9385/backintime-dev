import { describe, expect, it } from "vitest";

import css from "./styles.css?raw";

/** Return a property's value from the first top-level rule whose selector list contains `selector`. */
function declared(selector: string, property: string): string {
  const topLevel = css.split(/@media/)[0];
  for (const match of topLevel.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = match[1].split(",").map((item) => item.trim());
    if (!selectors.includes(selector)) continue;
    const value = new RegExp(`(?:^|;|\\s)${property}\\s*:\\s*([^;]+)`).exec(match[2]);
    if (value) return value[1].replace("!important", "").trim();
  }
  throw new Error(`No ${property} declared for ${selector}`);
}

function luminance(hex: string): number {
  const channels = hex
    .replace("#", "")
    .match(/../g)!
    .map((pair) => parseInt(pair, 16) / 255)
    .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(foreground: string, background: string): number {
  const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

const TEXT = 4.5;
const GRAPHIC = 3;
const WHITE = "#ffffff";

// [description, foreground selector/property, background (selector/property or literal), target]
const pairs: [string, [string, string], [string, string] | string, number][] = [
  ["sidebar service label", [".sidebar-foot", "color"], WHITE, TEXT],
  ["sidebar service state", [".service-state", "color"], WHITE, TEXT],
  ["navigation count", [".nav-count", "color"], [".nav-item.active", "background"], TEXT],
  ["coming-soon label", [".soon-label", "color"], WHITE, TEXT],
  ["muted status text", [".muted", "color"], WHITE, TEXT],
  ["service description", [".service-copy p", "color"], "#f8faff", TEXT],
  ["service metadata", [".service-meta", "color"], "#f8faff", TEXT],
  ["positive status text", [".positive-text", "color"], WHITE, TEXT],
  ["warning status text", [".warning-text", "color"], WHITE, TEXT],
  ["configured badge", [".configured-badge", "color"], [".configured-badge", "background"], TEXT],
  ["error banner title", [".error-banner", "color"], [".error-banner", "background"], TEXT],
  ["error banner message", [".error-banner p", "color"], [".error-banner", "background"], TEXT],
  ["copy success", [".copy-button.copied", "color"], [".copy-button.copied", "background"], TEXT],
  ["copy failure", [".copy-button.copy-error", "color"], [".copy-button.copy-error", "background"], TEXT],
  ["error banner icon", [".error-banner>span", "color"], [".error-banner>span", "background"], GRAPHIC],
  ["service warning symbol", [".service-symbol", "color"], [".service-symbol", "background"], GRAPHIC],
  ["service ready symbol", [".service-symbol.ready", "color"], [".service-symbol.ready", "background"], GRAPHIC],
  ["readiness passed icon", [".check-icon", "color"], [".check-icon", "background"], GRAPHIC],
  ["readiness warning icon", [".check-icon.warning", "color"], [".check-icon.warning", "background"], GRAPHIC],
  ["readiness error icon", [".check-icon.error", "color"], [".check-icon.error", "background"], GRAPHIC],
  ["offline status dot", [".status-dot", "background"], WHITE, GRAPHIC],
  ["online status dot", [".status-dot.online", "background"], WHITE, GRAPHIC],
  ["rsync warning dot", [".readiness-list dd .tiny-status", "background"], WHITE, GRAPHIC],
  ["focus outline", [".nav-item:focus-visible", "outline"], WHITE, GRAPHIC],
];

function color(value: string): string {
  const hex = /#([0-9a-f]{6}|[0-9a-f]{3})\b/i.exec(value);
  if (!hex) throw new Error(`Expected a hex color in "${value}"`);
  const digits = hex[1].length === 3 ? [...hex[1]].map((digit) => digit + digit).join("") : hex[1];
  return `#${digits}`;
}

describe("light-mode status and error contrast (WCAG 2.2 AA)", () => {
  it.each(pairs)("%s", (_, [fgSelector, fgProperty], background, target) => {
    const foreground = color(declared(fgSelector, fgProperty));
    const backgroundColor =
      typeof background === "string" ? background : color(declared(background[0], background[1]));

    expect(contrast(foreground, backgroundColor)).toBeGreaterThanOrEqual(target);
  });
});
