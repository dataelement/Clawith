/**
 * Theme color utility — generates accent palette from a single hex color
 * and applies it to CSS custom properties. Persists to localStorage.
 */

const STORAGE_KEY = "clawith-accent-color";

/** Convert hex to RGB */
function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}

/** Lighten a color by mixing with white */
function lighten(
  rgb: [number, number, number],
  amount: number,
): [number, number, number] {
  return rgb.map((c) => Math.min(255, Math.round(c + (255 - c) * amount))) as [
    number,
    number,
    number,
  ];
}

function rgbToHex(rgb: [number, number, number]): string {
  return "#" + rgb.map((c) => c.toString(16).padStart(2, "0")).join("");
}

/** Compute perceived luminance (0-1) */
function luminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((c) => c / 255);
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

/** Apply an accent color to the document */
export function applyAccentColor(hex: string) {
  const rgb = hexToRgb(hex);
  const lum = luminance(rgb);
  const root = document.documentElement;

  // Dark theme vars
  root.style.setProperty("--accent-primary", hex);
  root.style.setProperty("--accent-hover", rgbToHex(lighten(rgb, 0.2)));
  root.style.setProperty("--accent-subtle", `rgba(${rgb.join(",")}, 0.15)`);
  root.style.setProperty(
    "--accent-text",
    rgbToHex(lum > 0.6 ? rgb : lighten(rgb, 0.3)),
  );

  // For buttons: ensure text is readable
  root.style.setProperty(
    "--accent-btn-text",
    lum > 0.5 ? "#0a0a0f" : "#ffffff",
  );
}

/** Save accent color to localStorage */
export function saveAccentColor(hex: string) {
  localStorage.setItem(STORAGE_KEY, hex);
  applyAccentColor(hex);
}

/** Load and apply saved accent color (call on app init) */
export function loadSavedAccentColor() {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    applyAccentColor(saved);
  }
}

/** Get the currently saved accent color */
export function getSavedAccentColor(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

/** Reset to default (remove override, let CSS file values take effect) */
export function resetAccentColor() {
  localStorage.removeItem(STORAGE_KEY);
  const root = document.documentElement;
  root.style.removeProperty("--accent-primary");
  root.style.removeProperty("--accent-hover");
  root.style.removeProperty("--accent-subtle");
  root.style.removeProperty("--accent-text");
  root.style.removeProperty("--accent-btn-text");
}

/** Preset accent colors */
export const PRESET_COLORS = [
  { name: "Indigo", hex: "#5e6ad2" },
  { name: "Teal", hex: "#0abab5" },
  { name: "Blue", hex: "#3b82f6" },
  { name: "Cyan", hex: "#06b6d4" },
  { name: "Emerald", hex: "#10b981" },
  { name: "Rose", hex: "#f3217c" },
  { name: "Amber", hex: "#f59e0b" },
  { name: "Orange", hex: "#f97316" },
  { name: "Violet", hex: "#8b5cf6" },
  { name: "Slate", hex: "#64748b" },
];
