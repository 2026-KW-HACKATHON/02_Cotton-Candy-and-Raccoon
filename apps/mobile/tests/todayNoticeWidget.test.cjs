const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { loadTs } = require("./support/loadTs.cjs");
const { koreaDate, loadTodayNotice } = loadTs(
  path.join(__dirname, "../src/features/widget/todayNotice.ts"),
  {
    "../notices/api/noticeApi": {
      fetchTodayNotice: () => {
        throw new Error("Inject test API");
      },
    },
  },
);
const fixed = () => new Date("2026-10-08T16:00:00Z");
test("한국 자정 전후와 연도 경계를 현지 기기 시간대에 관계없이 판정한다", () => {
  assert.equal(koreaDate(new Date("2026-10-08T14:59:59Z")), "2026-10-08");
  assert.equal(koreaDate(new Date("2026-10-08T15:00:00Z")), "2026-10-09");
  assert.equal(koreaDate(new Date("2026-12-31T15:00:00Z")), "2027-01-01");
});
test("요약 없는 오늘 공문도 표시하고 빈 응답과 실패는 구분한다", async () => {
  const notice = {
    id: "1",
    publishedAt: "2026-10-09",
    title: "공지",
    hasSummary: false,
  };
  assert.deepEqual(await loadTodayNotice(async () => notice, fixed), {
    date: "2026-10-09",
    status: "ready",
    notice,
  });
  assert.equal(
    (await loadTodayNotice(async () => null, fixed)).status,
    "empty",
  );
  assert.equal(
    (
      await loadTodayNotice(async () => {
        throw new Error("offline");
      }, fixed)
    ).status,
    "error",
  );
});
test("이전 날짜 응답을 오늘 공문으로 사용하지 않는다", async () => {
  const state = await loadTodayNotice(
    async () => ({ publishedAt: "2026-10-08" }),
    fixed,
  );
  assert.equal(state.status, "error");
  assert.equal(state.notice, undefined);
});
test("조회 중 자정이 지나면 새 날짜로 재조회한다", async () => {
  const dates = ["2026-10-08T14:59:59Z", "2026-10-08T15:00:00Z"];
  const calls = [];
  const state = await loadTodayNotice(
    async (date) => {
      calls.push(date);
      return { publishedAt: date };
    },
    () => new Date(dates.shift() ?? "2026-10-08T15:00:01Z"),
  );
  assert.deepEqual(calls, ["2026-10-08", "2026-10-09"]);
  assert.equal(state.notice.publishedAt, "2026-10-09");
});
