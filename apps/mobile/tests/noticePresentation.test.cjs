const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

// 도메인은 React와 네이티브 런타임 없이 검증한다. TypeScript는 프로젝트의 설치본을 사용한다.
const source = fs.readFileSync(
  path.join(__dirname, "../src/features/notices/domain/noticePresentation.ts"),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const loaded = { exports: {} };
new Function("module", "exports", compiled)(loaded, loaded.exports);
const { getSummaryRows, isNoticeExpired, splitGlossaryText } = loaded.exports;

test("누락된 요약 항목은 숨기고 나머지 항목의 순서를 유지한다", () => {
  const notice = {
    audience: "주민",
    task: "신청",
    deadline: "10월 9일",
    caution: "  ",
  };
  assert.deepEqual(
    getSummaryRows(notice).map(({ label }) => label),
    ["대상", "할 일", "기한"],
  );
  assert.deepEqual(getSummaryRows({}), []);
});

test("신청기한 당일까지는 종료하지 않고 다음 날부터 종료한다", () => {
  const notice = { deadlineDate: "2026-10-09" };
  assert.equal(isNoticeExpired(notice, new Date(2026, 9, 9, 23, 59)), false);
  assert.equal(isNoticeExpired(notice, new Date(2026, 9, 10)), true);
});

test("명시적 날짜가 없거나 잘못된 날짜일 때 종료로 추정하지 않는다", () => {
  for (const deadlineDate of [undefined, "10월 9일", "2026-02-30", "invalid"]) {
    assert.equal(
      isNoticeExpired({ deadlineDate }, new Date(2026, 9, 10)),
      false,
    );
  }
});

test("겹치는 용어는 긴 표현을 우선하고 반복된 표현도 원문에 연결한다", () => {
  const terms = [
    { plain: "신청", original: "접수" },
    { plain: "먼저 신청한", original: "선착순" },
    { plain: "", original: "제외" },
  ];
  const text = "먼저 신청한 20명은 신청 후 확인합니다.";
  const parts = splitGlossaryText(text, terms);
  assert.equal(parts.map((part) => part.text).join(""), text);
  assert.deepEqual(
    parts.filter((part) => part.term).map((part) => part.term.original),
    ["선착순", "접수"],
  );
  assert.deepEqual(splitGlossaryText("안내", terms), [{ text: "안내" }]);
  assert.deepEqual(splitGlossaryText("", terms), []);
});
