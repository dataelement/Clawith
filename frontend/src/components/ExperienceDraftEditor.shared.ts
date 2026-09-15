import type { CSSProperties } from "react";

export const EXP_FIELDS: {
  key: "body" | "applicability";
  label: string;
  hint?: string;
  markdown?: boolean;
}[] = [
  { key: "body", label: "正文", markdown: true },
  {
    key: "applicability",
    label: "适用条件与失效信号",
    hint: "必填：此经验何时成立、出现什么信号说明已失效",
  },
];

export function bodyExcerpt(md?: string | null): string {
  return (md || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^\s*#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export const primaryBtn: CSSProperties = {
  padding: "8px 14px",
  borderRadius: 8,
  border: "none",
  cursor: "pointer",
  fontSize: 14,
  background: "var(--accent-primary)",
  color: "var(--text-inverse)",
  fontWeight: 500,
  flexShrink: 0,
};

export const secondaryBtn: CSSProperties = {
  padding: "7px 12px",
  borderRadius: 8,
  border: "1px solid var(--border-default)",
  cursor: "pointer",
  fontSize: 13,
  background: "var(--bg-card)",
  color: "var(--text-primary)",
};
