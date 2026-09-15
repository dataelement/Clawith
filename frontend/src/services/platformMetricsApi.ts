import { fetchJson } from "./api";
import {
  parseEnhancedMetrics,
  parseLeaderboards,
  parseTimeSeries,
} from "./platformMetricsResponse";
export type {
  AgentLeaderboardEntry,
  EnhancedMetrics,
  LeaderboardEntry,
  LeaderboardsResponse,
  TimeSeriesPoint,
} from "./platformMetricsResponse";

export const platformMetricsApi = {
  timeSeries: async (days: number, signal?: AbortSignal) => {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - days);
    const value = await fetchJson<unknown>(
      `/admin/metrics/timeseries?start_date=${start.toISOString()}&end_date=${end.toISOString()}`,
      { signal },
    );
    return parseTimeSeries(value);
  },
  leaderboards: async (signal?: AbortSignal) =>
    parseLeaderboards(
      await fetchJson<unknown>("/admin/metrics/leaderboards", { signal }),
    ),
  enhanced: async (signal?: AbortSignal) =>
    parseEnhancedMetrics(
      await fetchJson<unknown>("/admin/metrics/enhanced", { signal }),
    ),
};
