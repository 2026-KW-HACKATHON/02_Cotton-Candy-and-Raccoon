const { test } = require("node:test");
const assert = require("node:assert/strict");
require("./loadTypeScript.cjs");
const {
  locateSummaryEvidence,
  highlightDocumentParts,
} = require("../src/features/notices/domain/summaryEvidence.ts");
const {
  parseNotice,
} = require("../src/features/notices/api/noticeContract.ts");
const evidence = (field, excerpt, extra = {}) => ({
  field,
  excerpt,
  source_type: "text",
  verification: "text_matched",
  ...extra,
});

test("카드별 현재 본문 근거만 연결하며 이모지 뒤 UTF-16 위치도 보존한다", () => {
  const body = "😀주민 대상\n10월 12일까지\n방문 신청\n신분증 지참";
  const result = {
    evidence: [
      evidence("audience", "주민 대상"),
      evidence("dates", "10월 12일까지"),
      evidence("action", "방문 신청"),
      evidence("notes", "신분증 지참"),
      evidence("notes", "방문 신청"),
    ],
  };
  const ranges = locateSummaryEvidence(body, result);
  assert.equal(ranges.audience[0].start, 2);
  assert.equal(ranges.notes.length, 2);
  for (const [key, quote] of [
    ["audience", "주민 대상"],
    ["deadline", "10월 12일까지"],
    ["action", "방문 신청"],
  ]) {
    assert.equal(body.slice(ranges[key][0].start, ranges[key][0].end), quote);
  }
});

test("파일·미검증·중복·제목에만 있는 근거·바뀐 원문은 임의 강조하지 않는다", () => {
  const body = "중복 중복 새 날짜";
  for (const item of [
    evidence("notes", "중복"),
    evidence("notes", "제목"),
    evidence("dates", "옛 날짜"),
    evidence("notes", "새 날짜", { source_type: "document" }),
    evidence("notes", "새 날짜", { source_type: "image" }),
    evidence("notes", "새 날짜", { verification: null }),
    evidence("notes", "새 날짜", { source_id: "file-1" }),
    evidence("notes", "새 날짜", { page: 1 }),
  ])
    assert.ok(
      Object.values(locateSummaryEvidence(body, { evidence: [item] })).every(
        (r) => r.length === 0,
      ),
    );
  for (const result of [null, [], "bad", { evidence: [null, 1, {}] }])
    assert.doesNotThrow(() => locateSummaryEvidence(body, result));
});

test("겹친 근거를 표시해도 본문·줄바꿈·사전 메타데이터를 그대로 보존한다", () => {
  const term = { original: "공시송달", plain: "게시판 알림", meaning: "뜻" };
  const parts = [
    { text: "😀\n" },
    { text: "공시송달", term },
    { text: " 신청" },
  ];
  const result = highlightDocumentParts(parts, [
    { start: 4, end: 8 },
    { start: 6, end: 9 },
  ]);
  assert.equal(result.map((p) => p.text).join(""), "😀\n공시송달 신청");
  assert.equal(
    result
      .filter((p) => p.highlighted)
      .map((p) => p.text)
      .join(""),
    "시송달 신",
  );
  assert.ok(result.filter((p) => p.term).every((p) => p.term === term));
});

test("상세 계약은 공개 요약만 연결하고 실패 상태의 결과는 강조하지 않는다", () => {
  const row = {
    id: 1,
    title: "제목",
    registered_on: "2026-10-10",
    body_text: "주민 대상",
    card_summaries: { audience: "주민" },
    result: { evidence: [evidence("audience", "주민 대상")] },
  };
  assert.equal(
    parseNotice({ ...row, display_status: "summarized" }).summaryEvidence
      .audience.length,
    1,
  );
  assert.equal(
    parseNotice({ ...row, display_status: "failed" }).summaryEvidence.audience
      .length,
    0,
  );
});
