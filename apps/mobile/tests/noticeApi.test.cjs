const { test, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
require("./loadTypeScript.cjs");
const api = require("../src/features/notices/api/noticeApi.ts");
const {
  parseNotice,
  httpUrl,
} = require("../src/features/notices/api/noticeContract.ts");
const {
  filePreviewKind,
  fileDownloadName,
} = require("../src/features/notices/domain/noticeFiles.ts");
const originalFetch = global.fetch;
const envNames = [
  "EXPO_PUBLIC_SUPABASE_URL",
  "EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
];
const oldEnv = envNames.map((name) => process.env[name]);
test("위젯은 요약 상태와 무관하게 오늘 날짜 한 건을 조회한다", async () => {
  let requested;
  global.fetch = async (url) => {
    requested = new URL(url);
    return new Response(JSON.stringify([row()]));
  };
  const notice = await api.fetchTodayNotice("2026-10-09");
  assert.equal(notice.publishedAt, "2026.10.09");
  assert.equal(requested.pathname, "/rest/v1/app_notice_list");
  assert.equal(requested.searchParams.get("registered_on"), "eq.2026-10-09");
  assert.equal(requested.searchParams.get("order"), "registered_on.desc,id.desc");
  assert.equal(requested.searchParams.get("limit"), "1");
  assert.equal(requested.searchParams.has("display_status"), false);
  global.fetch = async () => new Response("[]");
  assert.equal(await api.fetchTodayNotice("2026-10-09"), null);
  await assert.rejects(api.fetchTodayNotice("2026-10-09,or(id.gt.0)"));
});
const row = (id = 1, extra = {}) => ({
  id,
  source: "seoul",
  title: "공문",
  registered_on: "2026-10-09",
  display_status: "none",
  category_code: 26,
  has_easy_text: false,
  ...extra,
});
beforeEach(() => {
  process.env.EXPO_PUBLIC_SUPABASE_URL = "https://example.test";
  process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_test";
});
afterEach(() => {
  global.fetch = originalFetch;
  envNames.forEach((name, i) => {
    if (oldEnv[i] === undefined) delete process.env[name];
    else process.env[name] = oldEnv[i];
  });
});
test("목록은 같은 날짜의 ID 커서로 다음 페이지를 조회한다", async () => {
  const urls = [];
  global.fetch = async (url) => {
    urls.push(new URL(url));
    return new Response(
      JSON.stringify(Array.from({ length: 20 }, (_, i) => row(40 - i))),
    );
  };
  const page = await api.fetchNoticePage();
  assert.deepEqual(page.nextCursor, { date: "2026-10-09", id: "21" });
  await api.fetchNoticePage(page.nextCursor);
  assert.equal(
    urls[1].searchParams.get("or"),
    "(registered_on.lt.2026-10-09,and(registered_on.eq.2026-10-09,id.lt.21))",
  );
});
test("없는 상세는 빈 결과이며 예시 데이터로 대체하지 않는다", async () => {
  let count = 0;
  global.fetch = async () => {
    count++;
    return new Response("[]");
  };
  assert.equal(await api.fetchNotice("walk"), null);
  assert.equal(count, 0);
  assert.equal(await api.fetchNotice("123"), null);
  assert.equal(count, 1);
});
test("보관 공문은 중복 ID를 제거하고 20개씩 조회한다", async () => {
  const urls = [];
  global.fetch = async (url) => {
    urls.push(new URL(url));
    return new Response("[]");
  };
  await api.fetchSavedNotices([
    ...Array.from({ length: 21 }, (_, i) => String(i + 1)),
    "1",
    "bad",
  ]);
  assert.equal(urls.length, 2);
  assert.equal(urls[1].searchParams.get("id"), "in.(21)");
});
test("설정과 권한 오류를 연결 오류와 구분한다", async () => {
  delete process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  await assert.rejects(api.fetchNotices(), (e) => e.code === "configuration");
  process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_test";
  global.fetch = async () => new Response("", { status: 403 });
  await assert.rejects(api.fetchNotices(), (e) => e.code === "configuration");
  global.fetch = async () => {
    throw new TypeError("offline");
  };
  await assert.rejects(api.fetchNotices(), (e) => e.code === "connection");
});
test("첨부 형식과 URL을 검증하며 HWPX 확장자를 유지한다", () => {
  assert.equal(httpUrl("javascript:alert(1)"), undefined);
  assert.equal(httpUrl("https://user:secret@example.test"), undefined);
  assert.throws(() =>
    parseNotice(
      row(1, { files: [{ id: 2, kind: "attachment", url: "file:///tmp/a" }] }),
    ),
  );
  const notice = parseNotice(
    row(1, {
      files: [
        { id: 2, kind: "attachment", url: "https://example.test/a.hwpx" },
      ],
    }),
  );
  assert.equal(filePreviewKind(notice.files[0]), "unsupported");
  assert.equal(fileDownloadName(notice.files[0]), "notice-file-2.hwpx");
  assert.equal(
    filePreviewKind({
      id: 3,
      kind: "attachment",
      url: "https://example.test/get?filename=a.PDF",
    }),
    "pdf",
  );
});
test("검토 중인 요약의 날짜로 마감을 단정하지 않는다", () => {
  const value = parseNotice(
    row(1, {
      display_status: "needs_review",
      deadline_on: "2026-10-01",
      card_summaries: { audience: null },
    }),
  );
  assert.equal(value.deadlineDate, undefined);
  assert.equal(value.audience, "원문에서 확인");
});
test("쉬운말 표현은 코드포인트 위치에만 연결한다", () => {
  const body = "🌿 통지 통지";
  const start = Array.from("공문\n🌿 ").length;
  const value = row(1, {
    body_text: body,
    has_easy_text: true,
    easy_body_text_present: true,
    easy_original_text: "공문\n" + body,
    easy_text: "공문\n🌿 안내 통지",
    easy_changes: [
      { start, end: start + 2, original: "통지", replacement: "안내" },
    ],
  });
  const notice = parseNotice(value);
  assert.equal(notice.easy, "🌿 안내 통지");
  assert.equal(notice.documentParts.original.filter((p) => p.term).length, 1);
  assert.equal(
    notice.documentParts.easy.map((p) => p.text).join(""),
    notice.easy,
  );
  assert.equal(
    parseNotice({ ...value, easy_text: "공문\n다른 문장" }).documentParts,
    undefined,
  );
  assert.equal(
    parseNotice({ ...value, easy_body_text_present: false }).hasEasyText,
    false,
  );
});

test("제목도 바뀐 쉬운말에서 본문만 추출하고 표현 위치를 유지한다", () => {
  const notice = parseNotice(
    row(1, {
      body_text: "통지",
      has_easy_text: true,
      easy_body_text_present: true,
      easy_original_text: "공문\n통지",
      easy_text: "알림\n안내",
      easy_changes: [
        { start: 0, end: 2, original: "공문", replacement: "알림" },
        { start: 3, end: 5, original: "통지", replacement: "안내" },
      ],
    }),
  );
  assert.equal(notice.easy, "안내");
  assert.equal(notice.documentParts.easy.map((p) => p.text).join(""), "안내");
  assert.equal(
    notice.documentParts.original.map((p) => p.text).join(""),
    "통지",
  );
});

test("카테고리와 오래된순은 서버 전체 범위에 적용하고 다음 커서도 같은 방향이다", async () => {
  const urls = [];
  global.fetch = async (url) => {
    urls.push(new URL(url));
    return new Response(
      JSON.stringify(Array.from({ length: 20 }, (_, i) => row(i + 1))),
    );
  };
  const options = { category: 26, oldestFirst: true };
  const page = await api.fetchNoticePage(null, "seoul", options);
  await api.fetchNoticePage(page.nextCursor, "seoul", options);
  for (const url of urls) {
    assert.equal(url.searchParams.get("category_code"), "eq.26");
    assert.equal(url.searchParams.get("source"), "eq.seoul");
    assert.equal(url.searchParams.get("order"), "registered_on.asc,id.asc");
  }
  assert.equal(
    urls[1].searchParams.get("or"),
    "(registered_on.gt.2026-10-09,and(registered_on.eq.2026-10-09,id.gt.20))",
  );
  await api.fetchNoticePage(null, "dong", { category: null });
  assert.equal(urls[2].searchParams.get("category_code"), "is.null");
});

test("상세는 근거와 누락을 조회하고 순서와 무관하게 파일 근거를 연결한다", async () => {
  let url;
  global.fetch = async (value) => {
    url = new URL(value);
    return new Response("[]");
  };
  await api.fetchNotice("1");
  for (const field of ["result", "file_references", "preparation_omissions"])
    assert.ok(url.searchParams.get("select").split(",").includes(field));
  const notice = parseNotice(
    row(1, {
      display_status: "needs_review",
      url: "https://example.test/notice",
      result: {
        evidence: [
          {
            excerpt: "신청은 10월까지",
            source_id: "media_2",
            source_type: "document",
            page: 2,
          },
        ],
      },
      file_references: [
        {
          source_id: "media_1",
          source_type: "document",
          files: [{ url: "https://example.test/wrong" }],
        },
        {
          source_id: "media_2",
          source_type: "document",
          files: [
            { url: "javascript:alert(1)" },
            { url: "https://example.test/right" },
          ],
        },
      ],
      preparation_omissions: [
        {
          reason_code: "unsupported_type",
          url: "https://example.test/file.xlsx",
        },
      ],
    }),
  );
  assert.deepEqual(notice.evidence, [
    {
      quote: "신청은 10월까지",
      label: "첨부 근거 · 2쪽",
      url: "https://example.test/right",
    },
  ]);
  assert.match(notice.omissions[0].message, /지원하지 않는 파일/);
  assert.equal(notice.omissions[0].url, "https://example.test/file.xlsx");
});

test("불완전한 근거 메타데이터는 안전한 원문으로 안내하고 요약 없는 공지는 근거를 숨긴다", () => {
  const data = {
    url: "https://example.test/notice",
    result: {
      evidence: [
        null,
        { excerpt: "근거", source_id: "media_1", source_type: "image" },
      ],
    },
    file_references: [null],
    preparation_omissions: [null, { url: "javascript:bad" }],
  };
  assert.equal(
    parseNotice(row(1, { ...data, display_status: "summarized" })).evidence[0]
      .url,
    data.url,
  );
  assert.deepEqual(parseNotice(row(1, data)).evidence, []);
  assert.equal(parseNotice(row(1, data)).omissions[0].url, data.url);
});

test("전체·기타·카테고리·정렬은 서로 다른 Query 캐시를 사용한다", () => {
  const path = require("node:path");
  const { loadTs } = require("./support/loadTs.cjs");
  const configs = [];
  const requests = [];
  const { useNotices } = loadTs(
    path.join(__dirname, "../src/features/notices/hooks/useNotices.ts"),
    {
      "@tanstack/react-query": {
        useInfiniteQuery: (config) => {
          configs.push(config);
          return config;
        },
      },
      "../api/noticeApi": { fetchNoticePage: (...args) => requests.push(args) },
      "../store/noticeScopeStore": {
        useNoticeScopeStore: (select) => select({ scope: "서울시" }),
      },
    },
  );
  useNotices();
  useNotices(true, { category: null });
  useNotices(true, { category: 26 });
  const sorted = useNotices(true, { category: 26, oldestFirst: true });
  assert.equal(new Set(configs.map((c) => JSON.stringify(c.queryKey))).size, 4);
  assert.ok(configs.every((c) => c.initialPageParam === null));
  assert.equal(configs[0].select, sorted.select);
  sorted.queryFn({ pageParam: { date: "2026-10-01", id: "10" } });
  assert.deepEqual(requests[0], [
    { date: "2026-10-01", id: "10" },
    "seoul",
    { category: 26, oldestFirst: true },
  ]);
});
