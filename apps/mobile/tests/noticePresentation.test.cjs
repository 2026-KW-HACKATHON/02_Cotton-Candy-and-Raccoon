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
const {
  formatSummaryText,
  getSummaryRows,
  isNoticeExpired,
  splitGlossaryText,
} = loaded.exports;

test("원문 용어도 반복·중첩 표현을 보존하며 같은 뜻풀이에 연결한다", () => {
  const terms = [
    { plain: "알림", original: "송달" },
    { plain: "게시판으로 알림", original: "공시송달" },
  ];
  const text = "공시송달 공고 후 송달과 공시송달을 확인합니다.";
  const parts = splitGlossaryText(text, terms, "original");
  assert.equal(parts.map((part) => part.text).join(""), text);
  assert.deepEqual(
    parts.filter((part) => part.term).map((part) => part.term.plain),
    ["게시판으로 알림", "알림", "게시판으로 알림"],
  );
  assert.deepEqual(splitGlossaryText("용어 없음", [], "original"), [
    { text: "용어 없음" },
  ]);
});

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

test("카드 문장과 명확한 목록만 줄바꿈하고 내용과 기존 개행을 보존한다", () => {
  const cases = [
    [
      "신분증을 지참하세요. 대리 신청은 불가능합니다.",
      "신분증을 지참하세요.\n대리 신청은 불가능합니다.",
    ],
    ["신청하셨나요? 결과를 확인하세요!", "신청하셨나요?\n결과를 확인하세요!"],
    [
      "“방문하세요.” 신분증이 필요합니다.",
      "“방문하세요.”\n신분증이 필요합니다.",
    ],
    ["준비물 • 신분증 • 신청서", "준비물\n• 신분증\n• 신청서"],
    ["1. 서류 준비 2. 방문 신청", "1. 서류 준비\n2. 방문 신청"],
    ["(1) 서류 준비 (2) 방문 신청", "(1) 서류 준비\n(2) 방문 신청"],
    ["1) 서류 준비 2) 방문 신청", "1) 서류 준비\n2) 방문 신청"],
    [
      "신청하세요.\n\n  방문하세요.\r\n문의하세요.",
      "신청하세요.\n\n  방문하세요.\r\n문의하세요.",
    ],
  ];
  for (const [input, expected] of cases) {
    const result = formatSummaryText(input);
    assert.equal(result, expected);
    assert.equal(result.replace(/\s/g, ""), input.replace(/\s/g, ""));
    assert.equal(formatSummaryText(result), result);
  }
});

test("날짜·소수·시간·URL·연락처·애매한 구간을 줄바꿈하지 않는다", () => {
  for (const text of [
    "2026.10.10. 09:00 ~ 18:00",
    "2026. 10. 10. 오후 6시까지",
    "1.5 배, 2.0% 할인",
    "전화 02-2116-1234",
    "준비물: 신분증·신청서",
    "https://example.com/안내. 다음 항목",
    "www.example.com/확인! 다음 항목",
    "문의: help@example.com 다음 항목",
    "신청... 추가 안내",
    "신청 대상: 주민",
    "1. 서류 준비 3. 방문 신청",
    "",
    "  ",
    "안내합니다.  ",
  ])
    assert.equal(formatSummaryText(text), text);
});

test("연도 없는 날짜 범위를 목록으로 나누지 않고 실제 번호 목록은 유지한다", () => {
  for (const text of [
    "접수 기간: 1. 1.부터 2. 2.까지",
    "접수 기간: 1. 1. ~ 2. 2.",
    "1. 10.(월)부터 2. 20.(금)까지",
    "기간: 1.\t1.부터 2.\t2.까지",
    "2026. 1. 1.부터 2026. 2. 2.까지",
  ]) {
    assert.equal(formatSummaryText(text), text);
  }
  assert.equal(
    formatSummaryText("기간: 1. 1.부터 2. 2.까지. 준비: 1. 신청서 2. 신분증"),
    "기간: 1. 1.부터 2. 2.까지.\n준비:\n1. 신청서\n2. 신분증",
  );
  assert.equal(
    formatSummaryText("1. 10명 모집 2. 20명 대기"),
    "1. 10명 모집\n2. 20명 대기",
  );
  assert.equal(
    formatSummaryText("1) 1. 1. 방문 2) 2. 2. 제출"),
    "1) 1. 1. 방문\n2) 2. 2. 제출",
  );
});
