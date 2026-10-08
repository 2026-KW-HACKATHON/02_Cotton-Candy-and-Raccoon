const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { loadTs } = require("./support/loadTs.cjs");
const { addKeyword, notificationNoticeId } = loadTs(path.join(__dirname, "../src/features/keyword-notifications/keywords.ts"));
test("normalize keywords, reject duplicates, bound length and count", () => {
  assert.deepEqual(addKeyword([], "  HEALTH   지원 "), ["health 지원"]);
  assert.throws(() => addKeyword(["health"], "HEALTH"));
  assert.throws(() => addKeyword([], " "));
  assert.throws(() => addKeyword([], "a".repeat(31)));
  assert.throws(() => addKeyword(Array.from({length:10}, (_,i) => `키워드${i}`), "신규"));
});
test("notification navigation accepts only keyword notice IDs", () => {
  assert.equal(notificationNoticeId({type:"keyword_notice",noticeId:"123"}), "123");
  for (const id of ["../settings", "0", "1e2", 1, null])
    assert.equal(notificationNoticeId({type:"keyword_notice",noticeId:id}), null);
  assert.equal(notificationNoticeId({type:"deadline",noticeId:"1"}), null);
});
