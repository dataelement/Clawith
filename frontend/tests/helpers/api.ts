import { vi } from 'vitest';

export function mockJsonResponse(body: unknown, init?: ResponseInit) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: init?.status ?? 200,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    }),
  );
}
