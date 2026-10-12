const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { loadTs } = require("./support/loadTs.cjs");
const { koreaDate, loadLatestNotice } = loadTs(
  path.join(__dirname, "../src/features/widget/latestNotice.ts"),
  {
    "../notices/api/noticeApi": {
      fetchLatestNotice: () => {
        throw new Error("Inject test API");
      },
    },
  },
);
const fixed = () => new Date("2026-10-11T16:00:00Z");

test("한국 자정 전후와 연도 경계를 현지 기기 시간대에 관계없이 판정한다", () => {
  assert.equal(koreaDate(new Date("2026-10-08T14:59:59Z")), "2026-10-08");
  assert.equal(koreaDate(new Date("2026-10-08T15:00:00Z")), "2026-10-09");
  assert.equal(koreaDate(new Date("2026-12-31T15:00:00Z")), "2027-01-01");
});
test("이전 날짜의 최신 공문도 표시하고 실제 등록일을 유지한다", async () => {
  const notice = {
    id: "1",
    publishedAt: "2026.10.09",
    title: "공지",
    hasSummary: false,
  };
  assert.deepEqual(await loadLatestNotice(async () => notice, fixed), {
    date: "2026-10-09",
    status: "ready",
    notice,
  });
});
test("공문이 없는 경우와 조회 실패를 구분하고 실패한 결과에 공문을 남기지 않는다", async () => {
  assert.equal((await loadLatestNotice(async () => null, fixed)).status, "empty");
  const state = await loadLatestNotice(async () => {
    throw new Error("offline");
  }, fixed);
  assert.equal(state.status, "error");
  assert.equal(state.notice, undefined);
});
test("조회 중 자정이 지나도 날짜 제한 없이 한 번 조회한 최신 공문을 표시한다", async () => {
  let calls = 0;
  let now = "2026-10-08T14:59:59Z";
  const state = await loadLatestNotice(
    async (...args) => {
      assert.deepEqual(args, []);
      calls++;
      now = "2026-10-08T15:00:01Z";
      return { publishedAt: "2026.10.08" };
    },
    () => new Date(now),
  );
  assert.equal(calls, 1);
  assert.equal(koreaDate(new Date(now)), "2026-10-09");
  assert.equal(state.status, "ready");
  assert.equal(state.date, "2026-10-08");
});
