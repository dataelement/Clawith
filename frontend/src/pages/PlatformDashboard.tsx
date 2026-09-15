import React, { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  Legend,
  type TooltipContentProps,
} from "recharts";
import { caughtErrorMessage } from "../services/apiError";
import {
  platformMetricsApi,
  type AgentLeaderboardEntry,
  type EnhancedMetrics,
  type LeaderboardEntry,
  type TimeSeriesPoint,
} from "../services/platformMetricsApi";

// ─── Helpers ───────────────────────────────────────────────

function formatTokens(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 1 : 0) + "K";
  if (n < 1_000_000_000)
    return (n / 1_000_000).toFixed(n < 10_000_000 ? 1 : 0) + "M";
  return (n / 1_000_000_000).toFixed(1) + "B";
}

function formatNumber(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n < 1000) return String(n);
  return n.toLocaleString();
}

// Color palette for pie/bar charts
const CHART_COLORS = [
  "#3b82f6",
  "#10b981",
  "#8b5cf6",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#ec4899",
  "#84cc16",
  "#f97316",
  "#6366f1",
];

// ─── InfoTooltip ─────────────────────────────────────────

/**
 * Small (i) icon that shows a tooltip on hover.
 * Pure CSS — no external tooltip library needed.
 */
const InfoTooltip = ({ text }: { text: string }) => (
  <span
    style={{
      position: "relative",
      display: "inline-flex",
      alignItems: "center",
      cursor: "help",
      marginLeft: "4px",
    }}
  >
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      style={{ opacity: 0.4 }}
    >
      <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
      <text
        x="8"
        y="12"
        textAnchor="middle"
        fontSize="10"
        fill="currentColor"
        fontWeight="600"
      >
        i
      </text>
    </svg>
    <span className="info-tooltip-popup">{text}</span>
  </span>
);

// Inject tooltip CSS once (hover-based, no JS state)
const tooltipStyleId = "__info-tooltip-style";
if (
  typeof document !== "undefined" &&
  !document.getElementById(tooltipStyleId)
) {
  const style = document.createElement("style");
  style.id = tooltipStyleId;
  style.textContent = `
        .info-tooltip-popup {
            visibility: hidden;
            opacity: 0;
            position: absolute;
            bottom: calc(100% + 8px);
            left: 50%;
            transform: translateX(-50%);
            background: var(--bg-elevated, #1e1e2e);
            color: var(--text-secondary, #ccc);
            border: 1px solid var(--border-subtle, #333);
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 11px;
            line-height: 1.5;
            white-space: normal;
            width: 240px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.25);
            transition: opacity 0.15s, visibility 0.15s;
            z-index: 1000;
            pointer-events: none;
        }
        span:hover > .info-tooltip-popup {
            visibility: visible;
            opacity: 1;
        }
    `;
  document.head.appendChild(style);
}

// ─── MetricCard ────────────────────────────────────────────

const MetricCard = ({
  label,
  value,
  tooltip,
}: {
  label: string;
  value: string;
  tooltip: string;
}) => (
  <div
    className="card"
    style={{
      flex: 1,
      minWidth: "200px",
      padding: "20px",
      display: "flex",
      flexDirection: "column",
      gap: "8px",
    }}
  >
    <div
      style={{
        display: "flex",
        alignItems: "center",
        fontSize: "12px",
        fontWeight: 600,
        color: "var(--text-tertiary)",
        textTransform: "uppercase",
        letterSpacing: "0.5px",
      }}
    >
      {label}
      <InfoTooltip text={tooltip} />
    </div>
    <div
      style={{
        fontSize: "28px",
        fontWeight: 700,
        color: "var(--text-primary)",
        fontFamily: "var(--font-mono, monospace)",
      }}
    >
      {value}
    </div>
  </div>
);

// ─── Main Component ──────────────────────────────────────

export default function PlatformDashboard() {
  useTranslation();
  const [timeRange, setTimeRange] = useState<30 | 7>(30);
  const [loadingStats, setLoadingStats] = useState(true);
  const [loadingLeaders, setLoadingLeaders] = useState(true);
  const [loadingEnhanced, setLoadingEnhanced] = useState(true);
  const [statsError, setStatsError] = useState("");
  const [leadersError, setLeadersError] = useState("");
  const [enhancedError, setEnhancedError] = useState("");

  const [timeSeriesData, setTimeSeriesData] = useState<TimeSeriesPoint[]>([]);
  const [topCompanies, setTopCompanies] = useState<LeaderboardEntry[]>([]);
  const [topAgents, setTopAgents] = useState<AgentLeaderboardEntry[]>([]);
  const [enhanced, setEnhanced] = useState<EnhancedMetrics | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    platformMetricsApi
      .timeSeries(timeRange, controller.signal)
      .then((data) => {
        setTimeSeriesData(data);
        setStatsError("");
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setStatsError(
            caughtErrorMessage(error) || "Failed to load platform trends.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingStats(false);
      });
    return () => controller.abort();
  }, [timeRange]);

  useEffect(() => {
    const controller = new AbortController();
    platformMetricsApi
      .leaderboards(controller.signal)
      .then((data) => {
        setTopCompanies(data.top_companies);
        setTopAgents(data.top_agents);
        setLeadersError("");
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLeadersError(
            caughtErrorMessage(error) || "Failed to load platform rankings.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingLeaders(false);
      });
    platformMetricsApi
      .enhanced(controller.signal)
      .then((data) => {
        setEnhanced(data);
        setEnhancedError("");
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setEnhancedError(
            caughtErrorMessage(error) || "Failed to load platform health data.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingEnhanced(false);
      });
    return () => controller.abort();
  }, []);

  // ─── Chart Tooltip ────────────────────────────────────

  const renderCustomTooltip = ({
    active,
    payload,
    label,
  }: TooltipContentProps) => {
    if (active && payload && payload.length) {
      return (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "8px",
            padding: "12px",
            boxShadow: "0 4px 12px rgba(0,0,0,0.1)",
            fontSize: "12px",
          }}
        >
          <div
            style={{
              fontWeight: 600,
              marginBottom: "8px",
              color: "var(--text-secondary)",
            }}
          >
            {label}
          </div>
          {payload.map((p, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                marginBottom: "4px",
              }}
            >
              <div
                style={{
                  width: "8px",
                  height: "8px",
                  borderRadius: "50%",
                  background: p.stroke || p.fill,
                }}
              />
              <span style={{ color: "var(--text-tertiary)" }}>{p.name}:</span>
              <span style={{ fontWeight: 500 }}>
                {typeof p.value === "number"
                  ? String(p.dataKey ?? "").includes("tokens")
                    ? formatTokens(p.value)
                    : formatNumber(p.value)
                  : String(p.value ?? "-")}
              </span>
            </div>
          ))}
        </div>
      );
    }
    return null;
  };

  // ─── Chart Cards ─────────────────────────────────────

  const renderChartCard = ({
    title,
    tooltip,
    dataKeyTotal,
    dataKeyNew,
    color,
  }: {
    title: string;
    tooltip: string;
    dataKeyTotal: string;
    dataKeyNew: string;
    color: string;
  }) => (
    <div
      className="card"
      style={{ flex: 1, minWidth: "300px", padding: "20px" }}
    >
      <div
        style={{
          fontSize: "13px",
          fontWeight: 600,
          marginBottom: "20px",
          color: "var(--text-secondary)",
          display: "flex",
          alignItems: "center",
        }}
      >
        {title}
        <InfoTooltip text={tooltip} />
      </div>
      <div style={{ height: "240px", width: "100%" }}>
        {loadingStats ? (
          <div
            style={{
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-tertiary)",
              fontSize: "12px",
            }}
          >
            Loading...
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={timeSeriesData}
              margin={{ top: 5, right: 5, left: -20, bottom: 5 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                vertical={false}
                stroke="var(--border-subtle)"
              />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(val) => val.substring(5)}
              />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                tickLine={false}
                axisLine={false}
                tickFormatter={formatTokens}
              />
              <Tooltip content={renderCustomTooltip} />
              <Line
                yAxisId="left"
                type="monotone"
                dataKey={dataKeyTotal}
                name="Cumulative"
                stroke={color}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
              <Line
                yAxisId="left"
                type="monotone"
                dataKey={dataKeyNew}
                name="New"
                stroke={color}
                opacity={0.3}
                strokeWidth={2}
                dot={false}
                strokeDasharray="4 4"
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );

  // ─── Multi-Line Chart (for Active Users DAU/WAU/MAU) ─

  const renderMultiLineChart = ({
    title,
    tooltip,
    lines,
  }: {
    title: string;
    tooltip: string;
    lines: { key: string; name: string; color: string }[];
  }) => (
    <div
      className="card"
      style={{ flex: 1, minWidth: "300px", padding: "20px" }}
    >
      <div
        style={{
          fontSize: "13px",
          fontWeight: 600,
          marginBottom: "20px",
          color: "var(--text-secondary)",
          display: "flex",
          alignItems: "center",
        }}
      >
        {title}
        <InfoTooltip text={tooltip} />
      </div>
      <div style={{ height: "240px", width: "100%" }}>
        {loadingStats ? (
          <div
            style={{
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-tertiary)",
              fontSize: "12px",
            }}
          >
            Loading...
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={timeSeriesData}
              margin={{ top: 5, right: 5, left: -20, bottom: 5 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                vertical={false}
                stroke="var(--border-subtle)"
              />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(val) => val.substring(5)}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip content={renderCustomTooltip} />
              <Legend iconSize={8} wrapperStyle={{ fontSize: "11px" }} />
              {lines.map((l) => (
                <Line
                  key={l.key}
                  type="monotone"
                  dataKey={l.key}
                  name={l.name}
                  stroke={l.color}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );

  // ─── Channel Pie Chart ──────────────────────────────────

  const renderChannelPieChart = () => {
    const data = enhanced?.channel_distribution || [];
    const total = data.reduce((sum, item) => sum + item.count, 0);
    return (
      <div
        className="card"
        style={{ flex: 1, minWidth: "300px", padding: "20px" }}
      >
        <div
          style={{
            fontSize: "13px",
            fontWeight: 600,
            marginBottom: "20px",
            color: "var(--text-secondary)",
            display: "flex",
            alignItems: "center",
          }}
        >
          Channel Distribution
          <InfoTooltip text="Distribution of chat sessions by source channel in the last 30 days" />
        </div>
        <div style={{ height: "280px", width: "100%" }}>
          {loadingEnhanced ? (
            <div
              style={{
                width: "100%",
                height: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--text-tertiary)",
                fontSize: "12px",
              }}
            >
              Loading...
            </div>
          ) : data.length === 0 ? (
            <div
              style={{
                width: "100%",
                height: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--text-tertiary)",
                fontSize: "12px",
              }}
            >
              No data
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="count"
                  nameKey="channel"
                  cx="50%"
                  cy="50%"
                  outerRadius={90}
                  innerRadius={50}
                  paddingAngle={2}
                  label={(entry) => {
                    const channel =
                      "channel" in entry && typeof entry.channel === "string"
                        ? entry.channel
                        : "";
                    const count =
                      "count" in entry && typeof entry.count === "number"
                        ? entry.count
                        : 0;
                    return `${channel} (${((count * 100) / total).toFixed(0)}%)`;
                  }}
                  labelLine={{ stroke: "var(--text-tertiary)", strokeWidth: 1 }}
                >
                  {data.map((item, i) => (
                    <Cell
                      key={item.channel}
                      fill={CHART_COLORS[i % CHART_COLORS.length]}
                    />
                  ))}
                </Pie>
                <Tooltip content={renderCustomTooltip} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    );
  };

  // ─── Tool Category Bar Chart ──────────────────────────

  const renderToolBarChart = () => {
    const data = enhanced?.tool_category_top10 || [];
    return (
      <div
        className="card"
        style={{ flex: 1, minWidth: "300px", padding: "20px" }}
      >
        <div
          style={{
            fontSize: "13px",
            fontWeight: 600,
            marginBottom: "20px",
            color: "var(--text-secondary)",
            display: "flex",
            alignItems: "center",
          }}
        >
          Top 10 Tool Categories
          <InfoTooltip text="Most popular tool categories across all active agent configurations" />
        </div>
        <div style={{ height: "280px", width: "100%" }}>
          {loadingEnhanced ? (
            <div
              style={{
                width: "100%",
                height: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--text-tertiary)",
                fontSize: "12px",
              }}
            >
              Loading...
            </div>
          ) : data.length === 0 ? (
            <div
              style={{
                width: "100%",
                height: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--text-tertiary)",
                fontSize: "12px",
              }}
            >
              No data
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data}
                layout="vertical"
                margin={{ top: 5, right: 20, left: 60, bottom: 5 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  horizontal={false}
                  stroke="var(--border-subtle)"
                />
                <XAxis
                  type="number"
                  tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  dataKey="category"
                  type="category"
                  tick={{ fontSize: 11, fill: "var(--text-secondary)" }}
                  tickLine={false}
                  axisLine={false}
                  width={55}
                />
                <Tooltip content={renderCustomTooltip} />
                <Bar
                  dataKey="count"
                  name="Enabled"
                  fill="#3b82f6"
                  radius={[0, 4, 4, 0]}
                  barSize={18}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    );
  };

  // ─── Churn Warning Table ───────────────────────────────

  const renderChurnTable = () => {
    const data = enhanced?.churn_warnings || [];
    return (
      <div className="card" style={{ padding: "0", overflow: "hidden" }}>
        <div
          style={{
            padding: "20px",
            fontSize: "13px",
            fontWeight: 600,
            color: "var(--text-secondary)",
            borderBottom: "1px solid var(--border-subtle)",
            display: "flex",
            alignItems: "center",
          }}
        >
          Churn Warning
          <InfoTooltip text="Companies that consumed >10M tokens but have had no activity in the past 14 days" />
        </div>
        {loadingEnhanced ? (
          <div
            style={{
              padding: "40px",
              textAlign: "center",
              fontSize: "12px",
              color: "var(--text-tertiary)",
            }}
          >
            Loading...
          </div>
        ) : data.length === 0 ? (
          <div
            style={{
              padding: "40px",
              textAlign: "center",
              fontSize: "12px",
              color: "var(--text-tertiary)",
            }}
          >
            No churn warnings — all active companies are healthy
          </div>
        ) : (
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "13px",
            }}
          >
            <thead>
              <tr
                style={{
                  borderBottom: "1px solid var(--border-subtle)",
                  color: "var(--text-tertiary)",
                  fontSize: "11px",
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                }}
              >
                <th
                  style={{
                    padding: "12px 20px",
                    textAlign: "left",
                    fontWeight: 600,
                  }}
                >
                  Company
                </th>
                <th
                  style={{
                    padding: "12px 20px",
                    textAlign: "right",
                    fontWeight: 600,
                  }}
                >
                  Total Tokens
                </th>
                <th
                  style={{
                    padding: "12px 20px",
                    textAlign: "right",
                    fontWeight: 600,
                  }}
                >
                  Last Active
                </th>
                <th
                  style={{
                    padding: "12px 20px",
                    textAlign: "right",
                    fontWeight: 600,
                  }}
                >
                  Days Inactive
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr
                  key={row.name}
                  style={{ borderBottom: "1px solid var(--border-subtle)" }}
                >
                  <td style={{ padding: "12px 20px", fontWeight: 500 }}>
                    {row.name}
                  </td>
                  <td
                    style={{
                      padding: "12px 20px",
                      textAlign: "right",
                      fontFamily: "var(--font-mono)",
                      fontSize: "12px",
                      color: "var(--text-secondary)",
                    }}
                  >
                    {formatTokens(row.total_tokens)}
                  </td>
                  <td
                    style={{
                      padding: "12px 20px",
                      textAlign: "right",
                      color: "var(--text-tertiary)",
                      fontSize: "12px",
                    }}
                  >
                    {row.last_active
                      ? new Date(row.last_active).toLocaleDateString()
                      : "Never"}
                  </td>
                  <td style={{ padding: "12px 20px", textAlign: "right" }}>
                    <span
                      style={{
                        background:
                          (row.days_inactive || 0) > 30
                            ? "rgba(239,68,68,0.15)"
                            : "rgba(245,158,11,0.15)",
                        color:
                          (row.days_inactive || 0) > 30 ? "#ef4444" : "#f59e0b",
                        padding: "2px 8px",
                        borderRadius: "10px",
                        fontSize: "12px",
                        fontWeight: 600,
                      }}
                    >
                      {row.days_inactive ?? "—"}d
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    );
  };

  // ─── Leaderboard ──────────────────────────────────────

  const renderLeaderboardCard = <T extends LeaderboardEntry>({
    title,
    tooltip,
    items,
    renderItem,
  }: {
    title: string;
    tooltip: string;
    items: T[];
    renderItem: (item: T, i: number) => React.ReactNode;
  }) => (
    <div
      className="card"
      style={{ flex: 1, minWidth: "300px", padding: "0", overflow: "hidden" }}
    >
      <div
        style={{
          padding: "20px",
          fontSize: "13px",
          fontWeight: 600,
          color: "var(--text-secondary)",
          borderBottom: "1px solid var(--border-subtle)",
          display: "flex",
          alignItems: "center",
        }}
      >
        {title}
        <InfoTooltip text={tooltip} />
      </div>
      {loadingLeaders ? (
        <div
          style={{
            padding: "40px",
            textAlign: "center",
            fontSize: "12px",
            color: "var(--text-tertiary)",
          }}
        >
          Loading...
        </div>
      ) : (
        <div>
          {items.map((item, i) => renderItem(item, i))}
          {items.length === 0 && (
            <div
              style={{
                padding: "20px",
                textAlign: "center",
                fontSize: "12px",
                color: "var(--text-tertiary)",
              }}
            >
              No data
            </div>
          )}
        </div>
      )}
    </div>
  );

  // ─── Render ───────────────────────────────────────────
  const loadErrors = [statsError, leadersError, enhancedError].filter(Boolean);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
      {/* Time Range Toggle */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <div
          style={{
            display: "flex",
            background: "var(--bg-secondary)",
            padding: "4px",
            borderRadius: "8px",
            border: "1px solid var(--border-subtle)",
          }}
        >
          {([7, 30] as const).map((d) => (
            <button
              key={d}
              onClick={() => {
                if (d === timeRange) return;
                setLoadingStats(true);
                setTimeRange(d);
              }}
              style={{
                padding: "6px 16px",
                fontSize: "12px",
                fontWeight: 500,
                borderRadius: "6px",
                background:
                  timeRange === d ? "var(--bg-primary)" : "transparent",
                color:
                  timeRange === d
                    ? "var(--text-primary)"
                    : "var(--text-tertiary)",
                boxShadow:
                  timeRange === d ? "0 1px 3px rgba(0,0,0,0.1)" : "none",
                border: "none",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              Last {d} Days
            </button>
          ))}
        </div>
      </div>

      {loadErrors.length > 0 && (
        <div
          role="alert"
          style={{
            padding: "12px 16px",
            borderRadius: "8px",
            border: "1px solid rgba(239,68,68,0.35)",
            background: "rgba(239,68,68,0.08)",
            color: "var(--error)",
            fontSize: "12px",
          }}
        >
          {loadErrors.join(" ")} Previously loaded data remains visible.
        </div>
      )}

      {/* Summary Cards */}
      <div style={{ display: "flex", gap: "20px", flexWrap: "wrap" }}>
        <MetricCard
          label="Avg Tokens / Session"
          value={
            enhanced ? formatTokens(enhanced.avg_tokens_per_session_30d) : "-"
          }
          tooltip="Average token consumption per chat session in the last 30 days. Calculated as total tokens / total sessions."
        />
        <MetricCard
          label="7-Day Retention"
          value={enhanced ? `${enhanced.retention_rate_7d}%` : "-"}
          tooltip={`Percentage of established companies (>14 days old) that were active last week and remain active this week. ${enhanced ? `${enhanced.retained_companies} of ${enhanced.last_week_active_companies} companies retained.` : ""}`}
        />
      </div>

      {/* Existing Trend Charts */}
      <div style={{ display: "flex", gap: "20px", flexWrap: "wrap" }}>
        {renderChartCard({
          title: "Companies",
          tooltip: "Cumulative and daily new company registrations",
          dataKeyTotal: "total_companies",
          dataKeyNew: "new_companies",
          color: "#3b82f6",
        })}
        {renderChartCard({
          title: "Users",
          tooltip: "Cumulative and daily new user registrations",
          dataKeyTotal: "total_users",
          dataKeyNew: "new_users",
          color: "#10b981",
        })}
        {renderChartCard({
          title: "Token Usage",
          tooltip: "Cumulative and daily token consumption across all agents",
          dataKeyTotal: "total_tokens",
          dataKeyNew: "new_tokens",
          color: "#8b5cf6",
        })}
      </div>

      {/* New Trend Charts: Sessions + Active Users */}
      <div style={{ display: "flex", gap: "20px", flexWrap: "wrap" }}>
        {renderChartCard({
          title: "Daily Sessions",
          tooltip:
            "Number of new chat sessions created per day and cumulative total",
          dataKeyTotal: "total_sessions",
          dataKeyNew: "new_sessions",
          color: "#f59e0b",
        })}
        {renderMultiLineChart({
          title: "Active Users",
          tooltip:
            "DAU: distinct users who sent at least 1 message that day. WAU: distinct users active in a rolling 7-day window. MAU: distinct users active in a rolling 30-day window.",
          lines: [
            { key: "dau", name: "DAU", color: "#10b981" },
            { key: "wau", name: "WAU", color: "#3b82f6" },
            { key: "mau", name: "MAU", color: "#8b5cf6" },
          ],
        })}
      </div>

      {/* Distribution Charts */}
      <div style={{ display: "flex", gap: "20px", flexWrap: "wrap" }}>
        {renderChannelPieChart()}
        {renderToolBarChart()}
      </div>

      {/* Leaderboards */}
      <div style={{ display: "flex", gap: "20px", flexWrap: "wrap" }}>
        {renderLeaderboardCard({
          title: "Top 20 Companies by Tokens",
          tooltip:
            "Companies ranked by total cumulative token consumption across all their agents",
          items: topCompanies,
          renderItem: (c, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "12px 20px",
                borderBottom: "1px solid var(--border-subtle)",
                fontSize: "13px",
              }}
            >
              <div
                style={{ display: "flex", gap: "12px", alignItems: "center" }}
              >
                <span
                  style={{
                    fontSize: "11px",
                    color: "var(--text-tertiary)",
                    width: "20px",
                  }}
                >
                  #{i + 1}
                </span>
                <span style={{ fontWeight: 500 }}>{c.name}</span>
              </div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "12px",
                  color: "var(--text-secondary)",
                }}
              >
                <div>{formatTokens(c.tokens)}</div>
                <div
                  style={{ fontSize: "10px", color: "var(--text-tertiary)" }}
                >
                  Cache {formatTokens(c.cache_read_tokens || 0)} ·{" "}
                  {Math.round((c.cache_hit_rate || 0) * 100)}%
                </div>
              </div>
            </div>
          ),
        })}
        {renderLeaderboardCard({
          title: "Top 20 Agents by Tokens",
          tooltip:
            "Individual agents ranked by total cumulative token consumption",
          items: topAgents,
          renderItem: (a, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "12px 20px",
                borderBottom: "1px solid var(--border-subtle)",
                fontSize: "13px",
              }}
            >
              <div
                style={{ display: "flex", gap: "12px", alignItems: "center" }}
              >
                <span
                  style={{
                    fontSize: "11px",
                    color: "var(--text-tertiary)",
                    width: "20px",
                  }}
                >
                  #{i + 1}
                </span>
                <div>
                  <div style={{ fontWeight: 500 }}>{a.name}</div>
                  <div
                    style={{ fontSize: "11px", color: "var(--text-tertiary)" }}
                  >
                    {a.company}
                  </div>
                </div>
              </div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "12px",
                  color: "var(--text-secondary)",
                }}
              >
                <div>{formatTokens(a.tokens)}</div>
                <div
                  style={{ fontSize: "10px", color: "var(--text-tertiary)" }}
                >
                  Cache {formatTokens(a.cache_read_tokens || 0)} ·{" "}
                  {Math.round((a.cache_hit_rate || 0) * 100)}%
                </div>
              </div>
            </div>
          ),
        })}
      </div>

      {/* Churn Warning */}
      {renderChurnTable()}
    </div>
  );
}
