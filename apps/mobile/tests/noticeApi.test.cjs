const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const { createClient } = require("@supabase/supabase-js");
const { QueryClient } = require("@tanstack/react-query");
const { loadTs } = require("./support/loadTs.cjs");
const src = path.join(__dirname, "../src/features/notices");
const { noticeFromRow } = loadTs(path.join(src, "api/noticeContract.ts"));
const snapshots = path.join(
  __dirname,
  "../../../services/pipeline/tests/e2e/cases",
);
function fixture(step = 1) {
  const doc = JSON.parse(
    fs.readFileSync(
      path.join(snapshots, `collect_one_easy_text/expected/step-${step}.json`),
      "utf8",
    ),
  );
  const db = doc.anon;
  return { id: 901, ...Object.values(db.app_notice_detail)[0] };
}
function api(responses) {
  const calls = [];
  const client = createClient(
    "https://example.supabase.co",
    "public-test-key",
    {
      auth: {
        persistSession: false,
        autoRefreshToken: false,
        detectSessionInUrl: false,
      },
      global: {
        fetch: async (input, init) => {
          calls.push({ url: new URL(input), init });
          const value = responses.shift();
          if (value instanceof Error) throw value;
          return new Response(JSON.stringify(value?.body ?? value), {
            status: value?.status ?? 200,
            headers: { "Content-Type": "application/json" },
          });
        },
      },
    },
  );
  return {
    calls,
    ...loadTs(path.join(src, "api/noticeApi.ts"), {
      "../../../shared/lib/supabase": { getSupabase: () => client },
    }),
  };
}
test("위젯은 요약 필터 없이 공개 뷰의 해당 날짜 1건을 고정 정렬로 조회한다", async () => {
  const a = api([[{ ...fixture(), registered_on: "2026-10-09" }], []]);
  const notice = await a.fetchTodayNotice("2026-10-09");
  assert.equal(notice.hasSummary, false);
  const url = a.calls[0].url;
  assert.equal(url.pathname, "/rest/v1/app_notice_list");
  assert.equal(url.searchParams.get("registered_on"), "eq.2026-10-09");
  assert.equal(url.searchParams.get("limit"), "1");
  assert.equal(url.searchParams.get("order"), "registered_on.desc,id.desc");
  assert.equal(url.searchParams.has("display_status"), false);
  assert.equal(await a.fetchTodayNotice("2026-10-09"), null);
  await assert.rejects(a.fetchTodayNotice("2026-10-09,or(id.gt.0)"));
});
test("실제 pipeline snapshot의 원문·분류·미생성 상태를 변환한다", () => {
  const row = fixture();
  const notice = noticeFromRow(row);
  assert.equal(notice.id, "901");
  assert.equal(notice.original, row.body_text);
  assert.equal(notice.category, "미분류");
  assert.equal(notice.displayStatus, "none");
  assert.equal(notice.hasSummary, false);
  assert.equal(notice.task, "원문에서 확인");
});
test("검토 결과의 카드·첨부 근거를 유지하고 날짜를 종료 상태로 추측하지 않는다", () => {
  const notice = noticeFromRow({
    ...fixture(),
    display_status: "needs_review",
    category_code: 26,
    deadline_on: "2020-01-01",
    card_summaries: { action: "참여하세요", notes: null },
    result: {
      evidence: [
        {
          excerpt: "신청 가능",
          source_type: "document",
          source_id: "media_2",
          page: 3,
        },
      ],
    },
    file_references: [
      {
        source_id: "media_1",
        source_type: "document",
        files: [{ url: "https://example.org/wrong" }],
      },
      {
        source_id: "media_2",
        source_type: "document",
        files: [{ url: "https://example.org/right" }],
      },
    ],
  });
  assert.equal(notice.task, "참여하세요");
  assert.equal(notice.category, "문화");
  assert.equal(notice.deadlineDate, undefined);
  assert.equal(notice.evidence[0].url, "https://example.org/right");
  assert.equal(notice.evidence[0].quote, "신청 가능");
});
test("페이지·출처·미분류 필터는 view와 명시 컬럼으로 요청한다", async () => {
  const a = api([
    Array.from({ length: 20 }, (_, i) => ({ ...fixture(), id: 100 - i })),
    [],
  ]);
  const page = await a.fetchNotices({
    source: "dong",
    category: "unclassified",
  });
  assert.equal(page.notices.length, 20);
  assert.equal(page.next.id, "81");
  await a.fetchNotices({ source: "dong", category: "unclassified" }, page.next);
  assert.equal(a.calls[0].url.pathname, "/rest/v1/app_notice_list");
  assert.equal(a.calls[0].url.searchParams.get("category_code"), "is.null");
  assert.equal(a.calls[0].url.searchParams.get("source"), "eq.dong");
  assert.equal(a.calls[0].url.searchParams.get("limit"), "20");
  assert.ok(!a.calls[0].url.searchParams.get("select").includes("*"));
  assert.match(a.calls[1].url.searchParams.get("or"), /id.lt.81/);
});
test("정상 0행 상세는 null이며 오류와 구별한다", async () => {
  const a = api([[], { status: 403, body: { code: "42501" } }]);
  assert.equal(await a.fetchNotice("901"), null);
  await assert.rejects(a.fetchNotice("901"), /권한/);
  assert.equal(await a.fetchNotice("invalid"), null);
  assert.equal(a.calls.length, 2);
});
test("새로고침 실패는 이전 캐시를 유지하고 정상 null은 캐시를 비운다", async () => {
  const a = api([[fixture()], { status: 500, body: { code: "error" } }, []]);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  const queryKey = ["notices", "detail", "901"];
  try {
    const first = await client.fetchQuery({
      queryKey,
      queryFn: () => a.fetchNotice("901"),
    });
    await assert.rejects(
      client.fetchQuery({ queryKey, queryFn: () => a.fetchNotice("901") }),
    );
    assert.deepEqual(client.getQueryData(queryKey), first);
    await client.fetchQuery({ queryKey, queryFn: () => a.fetchNotice("901") });
    assert.equal(client.getQueryData(queryKey), null);
  } finally {
    client.clear();
  }
});
test("보관함은 목록 첫 페이지와 무관하게 저장한 ID를 조회한다", async () => {
  const a = api([[fixture()]]);
  assert.equal((await a.fetchSavedNotices(["901"]))[0].id, "901");
  assert.equal(a.calls[0].url.searchParams.get("id"), "in.(901)");
});
test("잘못된 응답을 빈 목록 성공으로 처리하지 않는다", async () => {
  const a = api([{ malformed: true }]);
  await assert.rejects(a.fetchNotices(), /형식/);
  assert.throws(
    () => noticeFromRow({ ...fixture(), display_status: "new-status" }),
    /형식/,
  );
});
const { easyTextParts } = loadTs(path.join(src, "domain/easyTextParts.ts"));
test("코드포인트 좌표로 실제 바뀐 표현만 강조한다", () => {
  const parts = easyTextParts(
    "😀 방문 후 직접 가서 확인",
    "😀 직접 가서 후 직접 가서 확인",
    [{ start: 2, end: 4, original: "방문", replacement: "직접 가서" }],
  );
  assert.equal(parts.filter((p) => p.term).length, 1);
  assert.equal(
    parts.map((p) => p.text).join(""),
    "😀 직접 가서 후 직접 가서 확인",
  );
  assert.deepEqual(
    easyTextParts("원문", "변환", [
      { start: 99, end: 100, original: "문", replacement: "말" },
    ]),
    [{ text: "변환" }],
  );
});
