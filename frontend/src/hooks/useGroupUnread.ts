import { useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { groupApi } from '../services/groupApi';

/**
 * Total unread across every group, for the sidebar nav badge. Uses the same query keys the groups
 * page uses, so while that page is open nothing extra is fetched — the badge and the tree read the
 * same cache. On other pages the sidebar drives these queries with a slow poll (the real-time push
 * that would make polling unnecessary is not on the backend yet).
 */
export function useGroupUnread(): number {
    const {
        data: groups = [],
        isFetchedAfterMount,
        isRefetchError,
    } = useQuery({
        queryKey: ['groups'],
        queryFn: () => groupApi.list(),
        staleTime: 15_000,
        refetchOnMount: 'always',
    });
    const groupsReady = isFetchedAfterMount && !isRefetchError;

    const sessionQueries = useQueries({
        queries: (groupsReady ? groups : []).map((group) => ({
            queryKey: ['group-sessions', group.id],
            queryFn: () => groupApi.sessions(group.id),
            staleTime: 15_000,
            refetchInterval: 30_000,
        })),
    });

    return useMemo(
        () => sessionQueries.reduce(
            (total, query) =>
                total + ((query.data ?? []).reduce((sum, session) => sum + session.unread_count, 0)),
            0,
        ),
        [sessionQueries],
    );
}
