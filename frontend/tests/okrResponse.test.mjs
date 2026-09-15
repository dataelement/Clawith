import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  parseCompanyReportResponse,
  parseCompanyReports,
  parseMemberDailyReports,
  parseMembersWithoutOKR,
  parseObjectives,
  parseOKRSettings,
  parseOutreachResult,
  parsePeriods,
} from "../src/pages/okrResponse.ts";

const companyReport = {
  id: "report-1",
  report_type: "daily",
  period_start: "2026-08-26",
  period_end: "2026-08-26",
  period_label: "Aug 26",
  content: "Report",
  submitted_count: 2,
  missing_count: 1,
  needs_refresh: false,
  generated_at: "2026-08-26T01:00:00Z",
  updated_at: "2026-08-26T01:00:00Z",
};

test("OKR settings reject malformed successful responses", () => {
  const settings = {
    enabled: true,
    first_enabled_at: null,
    daily_report_enabled: true,
    daily_report_time: "09:00",
    daily_report_skip_non_workdays: true,
    weekly_report_enabled: true,
    weekly_report_day: 5,
    period_frequency: "quarterly",
    period_length_days: null,
    period_frequency_locked: true,
    okr_agent_id: "agent-1",
  };
  assert.deepEqual(parseOKRSettings(settings), settings);
  assert.throws(
    () => parseOKRSettings({ ...settings, weekly_report_day: "5" }),
    /response\.weekly_report_day/,
  );
});

test("OKR periods reject malformed successful list items", () => {
  const period = {
    start: "2026-07-01",
    end: "2026-09-30",
    label: "Q3 2026",
    is_current: true,
  };
  assert.deepEqual(parsePeriods([period]), [period]);
  assert.throws(
    () => parsePeriods([{ ...period, is_current: "yes" }]),
    /response\[0\]\.is_current/,
  );
});

test("OKR objectives validate nested key results", () => {
  const keyResult = {
    id: "kr-1",
    objective_id: "objective-1",
    title: "Ship",
    target_value: 10,
    current_value: 3,
    unit: null,
    focus_ref: null,
    status: "on_track",
    last_updated_at: "2026-08-26T01:00:00Z",
    created_at: "2026-08-01T01:00:00Z",
  };
  const objective = {
    id: "objective-1",
    title: "Launch",
    description: null,
    owner_type: "company",
    owner_id: null,
    owner_name: null,
    period_start: "2026-07-01",
    period_end: "2026-09-30",
    status: "active",
    created_at: "2026-08-01T01:00:00Z",
    key_results: [keyResult],
  };
  assert.deepEqual(parseObjectives([objective]), [objective]);
  assert.throws(
    () =>
      parseObjectives([
        {
          ...objective,
          key_results: [{ ...keyResult, current_value: "3" }],
        },
      ]),
    /response\[0\]\.key_results\[0\]\.current_value/,
  );
});

test("members-without-OKR validates nested warnings and error data", () => {
  const response = {
    period_start: "2026-07-01",
    period_end: "2026-09-30",
    company_okr_exists: true,
    okr_agent_id: "agent-1",
    members_without_okr: [
      {
        id: "member-1",
        type: "user",
        display_name: "Member",
        avatar_url: "",
        channel: null,
        channel_user_id: null,
        source_label: "Platform User",
      },
    ],
    tracked_user_ids: ["user-1"],
    tracked_agent_ids: [],
    total: 1,
    last_outreach_error: {
      message: "failed",
      timestamp: "2026-08-26T01:00:00Z",
      is_read: false,
    },
    channel_warnings: [
      {
        channel_type: "feishu",
        channel_display: "Feishu",
        affected_members: ["Member"],
        count: 1,
      },
    ],
  };
  assert.deepEqual(parseMembersWithoutOKR(response), response);
  assert.equal(
    parseMembersWithoutOKR({
      ...response,
      members_without_okr: [
        {
          ...response.members_without_okr[0],
          source_label: undefined,
        },
      ],
    }).members_without_okr[0].source_label,
    null,
  );
  assert.throws(
    () =>
      parseMembersWithoutOKR({
        ...response,
        channel_warnings: [
          { ...response.channel_warnings[0], affected_members: [1] },
        ],
      }),
    /response\.channel_warnings\[0\]\.affected_members\[0\]/,
  );
});

test("outreach parser accepts both backend variants and rejects unknown success", () => {
  assert.deepEqual(
    parseOutreachResult({
      status: "accepted",
      message: "Started",
      members_count: 2,
      okr_agent_id: "agent-1",
    }),
    {
      status: "accepted",
      message: "Started",
      members_count: 2,
      okr_agent_id: "agent-1",
    },
  );
  assert.deepEqual(
    parseOutreachResult({
      status: "no_action",
      message: "Nothing to do",
      okr_agent_id: "agent-1",
    }),
    {
      status: "no_action",
      message: "Nothing to do",
      okr_agent_id: "agent-1",
    },
  );
  assert.throws(
    () =>
      parseOutreachResult({
        status: "accepted",
        message: "Started",
        members_count: "2",
        okr_agent_id: "agent-1",
      }),
    /response\.members_count/,
  );
});

test("company report list and regeneration responses share strict validation", () => {
  assert.deepEqual(parseCompanyReports([companyReport]), [companyReport]);
  assert.deepEqual(parseCompanyReportResponse(companyReport), companyReport);
  assert.throws(
    () => parseCompanyReports([{ ...companyReport, report_type: "yearly" }]),
    /response\[0\]\.report_type/,
  );
  assert.throws(
    () =>
      parseCompanyReportResponse({ ...companyReport, submitted_count: 1.5 }),
    /response\.submitted_count/,
  );
});

test("member daily reports reject invalid member variants", () => {
  const report = {
    id: "user:user-1:2026-08-26",
    member_type: "user",
    member_id: "user-1",
    display_name: "Member",
    avatar_url: null,
    group_label: "Members",
    report_date: "2026-08-26",
    content: "Done",
    status: "submitted",
    submitted_at: "2026-08-26T01:00:00Z",
    updated_at: null,
  };
  assert.deepEqual(parseMemberDailyReports([report]), [report]);
  assert.throws(
    () => parseMemberDailyReports([{ ...report, member_type: "team" }]),
    /response\[0\]\.member_type/,
  );
});

test("OKR page treats all eight consumed JSON responses as unknown", () => {
  const source = readFileSync(
    new URL("../src/pages/OKR.tsx", import.meta.url),
    "utf8",
  );
  assert.equal(source.match(/fetchJson<unknown>/g)?.length, 8);
  assert.doesNotMatch(
    source,
    /fetchJson<(?!unknown>)(?:OKRSettings|Period\[\]|Objective\[\]|MembersWithoutOKRData|CompanyReport|MemberDailyReportItem)/,
  );
});
