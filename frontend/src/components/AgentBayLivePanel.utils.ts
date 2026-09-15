const MAX_LIVE_CODE_OUTPUT_CHARS = 120_000;
const LIVE_CODE_TRUNCATED_NOTICE =
  "\n\n[... older live output truncated ...]\n";

export function appendLiveCodeOutput(existing: string, chunk: string): string {
  const next = existing + chunk;
  if (next.length <= MAX_LIVE_CODE_OUTPUT_CHARS) return next;

  const keepChars = Math.max(
    0,
    MAX_LIVE_CODE_OUTPUT_CHARS - LIVE_CODE_TRUNCATED_NOTICE.length,
  );
  return LIVE_CODE_TRUNCATED_NOTICE + next.slice(-keepChars);
}
