export interface DirectHistoryRow {
    role?: string;
    cursor?: string;
    created_at?: string;
}

interface LoadDirectHistoryTurnOptions<T extends DirectHistoryRow> {
    before: string;
    pageSize: number;
    completeToolTurn: boolean;
    fetchPage: (before: string) => Promise<T[]>;
    maxContinuationPages?: number;
}

export interface DirectHistoryTurnPage<T extends DirectHistoryRow> {
    rows: T[];
    oldestCursor: string;
    hasMore: boolean;
    requestCount: number;
}

function rowCursor(row: DirectHistoryRow): string | null {
    return row.cursor ?? row.created_at ?? null;
}

function lastUserBoundaryIndex(rows: DirectHistoryRow[]): number {
    for (let index = rows.length - 1; index >= 0; index -= 1) {
        if (rows[index].role === 'user') return index;
    }
    return -1;
}

/**
 * Load one visual Direct Chat history unit. A backend page may split one collapsed Tool turn,
 * so continue backwards to its user-message boundary and publish the collected rows once.
 */
export async function loadDirectHistoryTurn<T extends DirectHistoryRow>({
    before,
    pageSize,
    completeToolTurn,
    fetchPage,
    maxContinuationPages = 250,
}: LoadDirectHistoryTurnOptions<T>): Promise<DirectHistoryTurnPage<T>> {
    let cursor = before;
    let rows: T[] = [];
    let hasMore = true;
    const seenCursors = new Set([before]);

    for (let requestCount = 1; requestCount <= maxContinuationPages; requestCount += 1) {
        const batch = await fetchPage(cursor);
        if (batch.length === 0) {
            return { rows, oldestCursor: cursor, hasMore: false, requestCount };
        }

        if (completeToolTurn) {
            const userBoundaryIndex = lastUserBoundaryIndex(batch);
            if (userBoundaryIndex >= 0) {
                const completedBoundaryRows = batch.slice(userBoundaryIndex);
                const boundaryCursor = rowCursor(completedBoundaryRows[0]);
                if (!boundaryCursor) throw new Error('Direct history boundary is missing its cursor');
                return {
                    rows: [...completedBoundaryRows, ...rows],
                    oldestCursor: boundaryCursor,
                    hasMore: batch.length >= pageSize || userBoundaryIndex > 0,
                    requestCount,
                };
            }
        }

        rows = [...batch, ...rows];
        const nextCursor = rowCursor(batch[0]);
        if (!nextCursor) throw new Error('Direct history page is missing its cursor');
        hasMore = batch.length >= pageSize;
        if (!completeToolTurn || !hasMore) {
            return { rows, oldestCursor: nextCursor, hasMore, requestCount };
        }
        if (seenCursors.has(nextCursor)) throw new Error('Direct history cursor did not advance');
        seenCursors.add(nextCursor);
        cursor = nextCursor;
    }

    throw new Error(`Direct history Tool turn exceeded ${maxContinuationPages} continuation pages`);
}
