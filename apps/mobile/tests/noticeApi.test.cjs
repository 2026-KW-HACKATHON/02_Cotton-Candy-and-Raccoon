const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { QueryClient } = require("@tanstack/react-query");

const source = fs.readFileSync(
  path.join(__dirname, "../src/features/notices/api/noticeApi.ts"),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const loaded = { exports: {} };
new Function("module", "exports", compiled)(loaded, loaded.exports);
const { fetchNotice, fetchNotices } = loaded.exports;

test("없는 공문은 조회 성공의 빈 결과로 캐시하고 오류와 구분한다", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  try {
    const queryKey = ["notices", "missing"];
    assert.equal(await client.fetchQuery({ queryKey, queryFn: () => fetchNotice("missing") }), null);
    assert.equal(client.getQueryState(queryKey).status, "success");
    assert.equal(client.getQueryState(queryKey).error, null);
    await assert.rejects(
      client.fetchQuery({
        queryKey: ["notices", "failed"],
        queryFn: async () => { throw new Error("Request failed"); },
      }),
      /Request failed/,
    );
    assert.equal(client.getQueryState(["notices", "failed"]).status, "error");
  } finally {
    client.clear();
  }
});

test("상세 조회는 목록과 동일한 공문·마감 시간을 제공한다", async () => {
  for (const notice of await fetchNotices()) {
    assert.deepEqual(await fetchNotice(notice.id), notice);
  }
  assert.equal((await fetchNotice("idea")).deadline, "10월 12일(월) 18:00까지");
});
