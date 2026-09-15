import type { ExperienceEntry } from "../services/api";

export function fmtDate(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

export function freshness(entry: ExperienceEntry): {
  label: string;
  stale: boolean;
} {
  if (entry.status !== "published") return { label: "", stale: false };
  if (!entry.last_reviewed_at) return { label: "未复核", stale: true };
  const date = new Date(entry.last_reviewed_at);
  const dateStr = `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  const age = Date.now() - date.getTime();
  const stale = age > 90 * 86400000;
  return { label: `${stale ? "复核超期" : "已复核"}（${dateStr}）`, stale };
}

const RETIRED_TTL_DAYS = 30;

export function retiredDaysLeft(entry: ExperienceEntry): number | null {
  if (entry.status !== "retired" || !entry.retired_at) return null;
  const date = new Date(entry.retired_at);
  if (Number.isNaN(date.getTime())) return null;
  const deadline = date.getTime() + RETIRED_TTL_DAYS * 86400000;
  return Math.max(0, Math.ceil((deadline - Date.now()) / 86400000));
}
