import { parseHttpErrorResponse } from "../../../services/apiError";

type ResponseParser<T> = (value: unknown) => T;

export function fetchAuth(url: string, options?: RequestInit): Promise<unknown>;
export function fetchAuth<T>(
  url: string,
  options: RequestInit | undefined,
  parser: ResponseParser<T>,
): Promise<T>;
export function fetchAuth<T>(
  url: string,
  options?: RequestInit,
  parser?: ResponseParser<T>,
): Promise<T | unknown> {
  const token = localStorage.getItem("token");
  return fetch(`/api${url}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options?.headers,
    },
  }).then(async (response) => {
    if (!response.ok) throw await parseHttpErrorResponse(response);
    if (response.status === 204) return parser ? parser(undefined) : undefined;
    const data: unknown = await response.json();
    return parser ? parser(data) : data;
  });
}
