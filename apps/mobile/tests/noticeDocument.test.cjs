const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { loadTs } = require("./support/loadTs.cjs");
const { noticeDocumentParts } = loadTs(
  path.join(__dirname, "../src/features/notices/domain/easyTextParts.ts"),
);

function notice(overrides = {}) {
  return {
    original: "😀공시송달 후 공시송달",
    easyOriginal: "😀공시송달 후 공시송달",
    easy: "😀공시송달 후 게시판으로 알림",
    hasEasyText: true,
    easyChanges: [
      {
        start: 8,
        end: 12,
        original: "공시송달",
        replacement: "게시판으로 알림",
      },
    ],
    ...overrides,
  };
}

test("원문에서는 검증된 변환 위치만 연결하고 같은 단어의 다른 위치는 보존한다", () => {
  const value = notice();
  const parts = noticeDocumentParts(value, false);
  assert.equal(parts.map((part) => part.text).join(""), value.original);
  assert.deepEqual(
    parts.filter((part) => part.term),
    [
      {
        text: "공시송달",
        term: { original: "공시송달", plain: "게시판으로 알림" },
      },
    ],
  );
  assert.equal(parts[0].text, "😀공시송달 후 ");
});

test("쉬운말에서는 서버 결과를 보존하고 변환 표현을 원문에 연결한다", () => {
  const value = notice();
  const parts = noticeDocumentParts(value, true);
  assert.equal(parts.map((part) => part.text).join(""), value.easy);
  assert.equal(parts.find((part) => part.term).text, "게시판으로 알림");
});

test("쉬운말 미생성 또는 빈 결과에서는 현재 원문을 표시한다", () => {
  for (const overrides of [{ hasEasyText: false }, { easy: "" }]) {
    const value = notice(overrides);
    assert.deepEqual(noticeDocumentParts(value, true), [
      { text: value.original },
    ]);
  }
  assert.match(
    noticeDocumentParts(notice({ original: "", hasEasyText: false }), true)[0]
      .text,
    /본문 텍스트가 없습니다/,
  );
});

test("현재 원문이 변경되면 이전 변환 위치를 새 원문에 연결하지 않는다", () => {
  const value = notice({ original: "새 공고 본문의 공시송달" });
  assert.deepEqual(noticeDocumentParts(value, false), [
    { text: value.original },
  ]);
});

test("좌표 또는 변환 결과가 불일치하면 강조 없이 각 본문을 보존한다", () => {
  for (const overrides of [
    {
      easyChanges: [
        {
          start: 2,
          end: 6,
          original: "공시송달",
          replacement: "게시판으로 알림",
        },
      ],
    },
    { easy: "변환 위치와 다른 쉬운말 본문" },
    { easyChanges: null },
  ]) {
    const value = notice(overrides);
    assert.deepEqual(noticeDocumentParts(value, false), [
      { text: value.original },
    ]);
    assert.deepEqual(noticeDocumentParts(value, true), [{ text: value.easy }]);
  }
});
