import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const okrPage = readFileSync(
  new URL("../src/pages/OKR.tsx", import.meta.url),
  "utf8",
);

test("OKR selections fall back from current query data without effect resets", () => {
  assert.match(okrPage, /const selectedPeriod = useMemo/);
  assert.match(okrPage, /selectedStillExists \?\?/);
  assert.match(okrPage, /periods\.find\(\(period\) => period\.is_current\)/);
  assert.match(
    okrPage,
    /const visibleActiveTab = settings\?\.daily_report_enabled/,
  );
  assert.doesNotMatch(okrPage, /setSelectedPeriod\(current\)/);
  assert.doesNotMatch(
    okrPage,
    /useEffect\(\(\) => \{\s*if \(settings && !settings\.daily_report_enabled\)/,
  );
});

test("OKR report and outreach fallbacks remain derived from live data", () => {
  assert.match(
    okrPage,
    /const visibleNudgeResult = data\?\.last_outreach_error \? null : nudgeResult/,
  );
  assert.match(okrPage, /const visibleExpandedCompanyReportId =/);
  assert.match(okrPage, /const selectedMemberReport =/);
  assert.doesNotMatch(okrPage, /setExpandedCompanyReportId\(null\)/);
  assert.doesNotMatch(okrPage, /setSelectedMemberReportId\(null\)/);
});
