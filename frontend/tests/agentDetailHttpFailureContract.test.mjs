import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/pages/agent-detail/AgentDetailPage.tsx", import.meta.url),
  "utf8",
);
const fetchAuthSource = readFileSync(
  new URL("../src/pages/agent-detail/utils/fetchAuth.ts", import.meta.url),
  "utf8",
);

function between(start, end) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
  assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("failed session deletion and expiry updates cannot publish local success", () => {
  const deletion = between("const deleteSession = async", "// Expiry editor");
  assert.ok(
    deletion.indexOf("await requireOk(response)") <
      deletion.indexOf("closeSessionSocket"),
  );
  assert.ok(
    deletion.indexOf("await requireOk(response)") <
      deletion.indexOf("setActiveSession(null)"),
  );

  const expiry = between("const saveExpiry = async", "const [chatMessages");
  assert.ok(
    expiry.indexOf("await requireOk(response)") <
      expiry.indexOf("setShowExpiryModal(false)"),
  );
});

test("session loaders retain prior state and reject malformed success payloads", () => {
  const mySessions = between(
    "const fetchMySessions = async",
    "const fetchAllSessions",
  );
  assert.match(mySessions, /await requireOk\(res\)/);
  assert.match(mySessions, /chatSessionsFromUnknown\(payload\)/);
  assert.match(mySessions, /setSessionsError\(/);
  assert.doesNotMatch(mySessions, /catch[\s\S]*setSessions\(\[\]\)/);

  const allSessions = between(
    "const fetchAllSessions = async",
    "const selectSession",
  );
  assert.match(allSessions, /await requireOk\(res\)/);
  assert.match(allSessions, /chatSessionsFromUnknown\(payload\)/);
  assert.match(allSessions, /setAllSessionsError\(/);
  assert.doesNotMatch(allSessions, /catch[\s\S]*setAllSessions\(\[\]\)/);

  assert.match(
    source,
    /if \(!Array\.isArray\(value\)\) throw new Error\("Invalid session list response"\)/,
  );
  assert.match(
    source,
    /if \(sessions\.some\(\(session\) => session === null\)\)/,
  );
  assert.match(source, /error: reflectionSessionsError/);
  assert.match(source, /reflectionMessageErrors\[session\.id\]/);
});

test("agent-detail authenticated fetches use unknown JSON and canonical HTTP errors", () => {
  assert.match(
    fetchAuthSource,
    /const data: unknown = await response\.json\(\)/,
  );
  assert.match(
    fetchAuthSource,
    /throw await parseHttpErrorResponse\(response\)/,
  );
  assert.match(fetchAuthSource, /return parser \? parser\(data\) : data/);
});
