export function normalizeDepartmentItems<T>(response: unknown): T[] {
  if (Array.isArray(response)) return response as T[];
  if (response && typeof response === 'object' && Array.isArray((response as { items?: unknown }).items)) {
    return (response as { items: T[] }).items;
  }
  return [];
}

export function getDepartmentTotalMembers(response: unknown): number | null {
  if (
    response &&
    typeof response === 'object' &&
    typeof (response as { total_member?: unknown }).total_member === 'number'
  ) {
    return (response as { total_member: number }).total_member;
  }
  return null;
}

export function normalizeMembersResponse<T>(response: unknown): T[] {
  return Array.isArray(response) ? (response as T[]) : [];
}
