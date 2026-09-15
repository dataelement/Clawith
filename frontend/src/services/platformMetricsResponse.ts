export interface TimeSeriesPoint {
  date: string;
  total_companies: number;
  new_companies: number;
  total_users: number;
  new_users: number;
  total_tokens: number;
  new_tokens: number;
  total_sessions: number;
  new_sessions: number;
  dau: number;
  wau: number;
  mau: number;
}

export interface LeaderboardEntry {
  name: string;
  tokens: number;
  cache_read_tokens?: number;
  cache_hit_rate?: number;
}

export interface AgentLeaderboardEntry extends LeaderboardEntry {
  company: string;
}

export interface ChannelDistribution {
  channel: string;
  count: number;
}

export interface ToolCategoryCount {
  category: string;
  count: number;
}

export interface ChurnWarning {
  name: string;
  total_tokens: number;
  last_active: string | null;
  days_inactive: number | null;
}

export interface EnhancedMetrics {
  avg_tokens_per_session_30d: number;
  retention_rate_7d: number;
  retained_companies: number;
  last_week_active_companies: number;
  channel_distribution: ChannelDistribution[];
  tool_category_top10: ToolCategoryCount[];
  churn_warnings: ChurnWarning[];
}

export interface LeaderboardsResponse {
  top_companies: LeaderboardEntry[];
  top_agents: AgentLeaderboardEntry[];
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isTimeSeriesPoint(value: unknown): value is TimeSeriesPoint {
  return (
    typeof value === "object" &&
    value !== null &&
    "date" in value &&
    typeof value.date === "string" &&
    "total_companies" in value &&
    isNumber(value.total_companies) &&
    "new_companies" in value &&
    isNumber(value.new_companies) &&
    "total_users" in value &&
    isNumber(value.total_users) &&
    "new_users" in value &&
    isNumber(value.new_users) &&
    "total_tokens" in value &&
    isNumber(value.total_tokens) &&
    "new_tokens" in value &&
    isNumber(value.new_tokens) &&
    "total_sessions" in value &&
    isNumber(value.total_sessions) &&
    "new_sessions" in value &&
    isNumber(value.new_sessions) &&
    "dau" in value &&
    isNumber(value.dau) &&
    "wau" in value &&
    isNumber(value.wau) &&
    "mau" in value &&
    isNumber(value.mau)
  );
}

function isLeaderboardEntry(value: unknown): value is LeaderboardEntry {
  return (
    typeof value === "object" &&
    value !== null &&
    "name" in value &&
    typeof value.name === "string" &&
    "tokens" in value &&
    isNumber(value.tokens) &&
    (!("cache_read_tokens" in value) ||
      value.cache_read_tokens === undefined ||
      isNumber(value.cache_read_tokens)) &&
    (!("cache_hit_rate" in value) ||
      value.cache_hit_rate === undefined ||
      isNumber(value.cache_hit_rate))
  );
}

function isAgentLeaderboardEntry(
  value: unknown,
): value is AgentLeaderboardEntry {
  return (
    isLeaderboardEntry(value) &&
    "company" in value &&
    typeof value.company === "string"
  );
}

function isChannelDistribution(value: unknown): value is ChannelDistribution {
  return (
    typeof value === "object" &&
    value !== null &&
    "channel" in value &&
    typeof value.channel === "string" &&
    "count" in value &&
    isNumber(value.count)
  );
}

function isToolCategoryCount(value: unknown): value is ToolCategoryCount {
  return (
    typeof value === "object" &&
    value !== null &&
    "category" in value &&
    typeof value.category === "string" &&
    "count" in value &&
    isNumber(value.count)
  );
}

function isChurnWarning(value: unknown): value is ChurnWarning {
  return (
    typeof value === "object" &&
    value !== null &&
    "name" in value &&
    typeof value.name === "string" &&
    "total_tokens" in value &&
    isNumber(value.total_tokens) &&
    "last_active" in value &&
    (value.last_active === null || typeof value.last_active === "string") &&
    "days_inactive" in value &&
    (value.days_inactive === null || isNumber(value.days_inactive))
  );
}

export function parseTimeSeries(value: unknown): TimeSeriesPoint[] {
  if (!Array.isArray(value) || !value.every(isTimeSeriesPoint)) {
    throw new Error("Invalid platform time series response");
  }
  return value;
}

export function parseLeaderboards(value: unknown): LeaderboardsResponse {
  if (
    typeof value !== "object" ||
    value === null ||
    !("top_companies" in value) ||
    !Array.isArray(value.top_companies) ||
    !value.top_companies.every(isLeaderboardEntry) ||
    !("top_agents" in value) ||
    !Array.isArray(value.top_agents) ||
    !value.top_agents.every(isAgentLeaderboardEntry)
  ) {
    throw new Error("Invalid platform leaderboards response");
  }
  return {
    top_companies: value.top_companies,
    top_agents: value.top_agents,
  };
}

export function parseEnhancedMetrics(value: unknown): EnhancedMetrics {
  if (
    typeof value !== "object" ||
    value === null ||
    !("avg_tokens_per_session_30d" in value) ||
    !isNumber(value.avg_tokens_per_session_30d) ||
    !("retention_rate_7d" in value) ||
    !isNumber(value.retention_rate_7d) ||
    !("retained_companies" in value) ||
    !isNumber(value.retained_companies) ||
    !("last_week_active_companies" in value) ||
    !isNumber(value.last_week_active_companies) ||
    !("channel_distribution" in value) ||
    !Array.isArray(value.channel_distribution) ||
    !value.channel_distribution.every(isChannelDistribution) ||
    !("tool_category_top10" in value) ||
    !Array.isArray(value.tool_category_top10) ||
    !value.tool_category_top10.every(isToolCategoryCount) ||
    !("churn_warnings" in value) ||
    !Array.isArray(value.churn_warnings) ||
    !value.churn_warnings.every(isChurnWarning)
  ) {
    throw new Error("Invalid platform enhanced metrics response");
  }
  return {
    avg_tokens_per_session_30d: value.avg_tokens_per_session_30d,
    retention_rate_7d: value.retention_rate_7d,
    retained_companies: value.retained_companies,
    last_week_active_companies: value.last_week_active_companies,
    channel_distribution: value.channel_distribution,
    tool_category_top10: value.tool_category_top10,
    churn_warnings: value.churn_warnings,
  };
}
