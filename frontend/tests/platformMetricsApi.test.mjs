import assert from "node:assert/strict";
import test from "node:test";

import {
  parseEnhancedMetrics,
  parseLeaderboards,
  parseTimeSeries,
} from "../src/services/platformMetricsResponse.ts";

const point = {
  date: "2026-08-26",
  total_companies: 10,
  new_companies: 1,
  total_users: 20,
  new_users: 2,
  total_tokens: 300,
  new_tokens: 30,
  total_sessions: 40,
  new_sessions: 4,
  dau: 5,
  wau: 6,
  mau: 7,
};

test("platform metric parsers accept complete endpoint contracts", () => {
  assert.deepEqual(parseTimeSeries([point]), [point]);
  assert.deepEqual(
    parseLeaderboards({
      top_companies: [{ name: "Acme", tokens: 10 }],
      top_agents: [{ name: "A", company: "Acme", tokens: 8 }],
    }),
    {
      top_companies: [{ name: "Acme", tokens: 10 }],
      top_agents: [{ name: "A", company: "Acme", tokens: 8 }],
    },
  );
  assert.equal(
    parseEnhancedMetrics({
      avg_tokens_per_session_30d: 12,
      retention_rate_7d: 50,
      retained_companies: 2,
      last_week_active_companies: 4,
      channel_distribution: [{ channel: "web", count: 3 }],
      tool_category_top10: [{ category: "file", count: 2 }],
      churn_warnings: [
        {
          name: "Old Co",
          total_tokens: 10000001,
          last_active: null,
          days_inactive: null,
        },
      ],
    }).retention_rate_7d,
    50,
  );
});

test("platform metric parsers reject incomplete or malformed payloads", () => {
  assert.throws(
    () => parseTimeSeries([{ ...point, dau: "5" }]),
    /time series/i,
  );
  assert.throws(
    () => parseLeaderboards({ top_companies: [{ name: "Acme" }] }),
    /leaderboards/i,
  );
  assert.throws(
    () =>
      parseEnhancedMetrics({
        avg_tokens_per_session_30d: 12,
        retention_rate_7d: 50,
      }),
    /enhanced metrics/i,
  );
});
