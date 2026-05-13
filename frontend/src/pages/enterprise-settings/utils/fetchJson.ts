export async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api${url}`, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = body.detail;
        const msg = Array.isArray(detail)
            ? detail.map((e: any) => e.msg || JSON.stringify(e)).join('; ')
            : (typeof detail === 'string' ? detail : 'Error');
        throw new Error(msg);
    }
    if (res.status === 204) return undefined as T;
    return res.json();
}
