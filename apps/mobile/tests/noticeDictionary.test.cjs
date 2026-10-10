const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
require("./loadTypeScript.cjs");
const {
  withNoticeDictionary,
  currentDictionaryTerm,
} = require("../src/features/notices/domain/noticeDictionary.ts");
const { loadTs } = require("./support/loadTs.cjs");

const notice = {
  id: "6",
  title: "😀공문",
  original: "😀 지참 후 지참",
  easy: "😀 가져온 후 지참",
  hasEasyText: true,
  documentParts: {
    original: [
      { text: "기존 치환 표시", term: { original: "지참", plain: "가져온" } },
    ],
    easy: [
      {
        text: "😀 가져온 후 지참",
        term: { original: "지참", plain: "가져온" },
      },
    ],
  },
};
function payload(status = "found") {
  const original = notice.title + "\n" + notice.original;
  const start = Array.from(notice.title + "\n😀 지참 후 ").length;
  return {
    notice_id: 6,
    original_text: original,
    dictionary_status: "complete",
    dictionary_candidates: [
      {
        start,
        end: start + 2,
        original: "지참",
        query_word: "지참",
        lookup_status: status,
        dictionary:
          status === "found"
            ? {
                query_word: "지참",
                contract_version: "stdict-v1",
                status: "found",
                entries: [
                  {
                    target_code: "123",
                    headword: "지참",
                    source_url:
                      "https://stdict.korean.go.kr/search/searchView.do?word_no=123",
                    senses: [
                      {
                        definition: "물건을 가지고 가거나 옴.",
                        part_of_speech: "명사",
                      },
                      { definition: "다른 뜻풀이", part_of_speech: "명사" },
                    ],
                  },
                ],
              }
            : status === "not_found"
              ? {
                  query_word: "지참",
                  contract_version: "stdict-v1",
                  status,
                  entries: [],
                }
              : null,
      },
    ],
  };
}
test("치환하지 않은 후보도 제목·이모지의 코드포인트 위치대로 연결한다", () => {
  const result = withNoticeDictionary(notice, payload());
  assert.equal(
    result.documentParts.original.map((p) => p.text).join(""),
    notice.original,
  );
  const terms = result.documentParts.original.filter((p) => p.term);
  assert.equal(terms.length, 1);
  assert.equal(result.documentParts.original[0].text, "😀 지참 후 ");
  assert.equal(terms[0].term.dictionary.entries[0].senses.length, 2);
  assert.equal(
    terms[0].term.dictionary.entries[0].senses[0].definition,
    "물건을 가지고 가거나 옴.",
  );
  assert.equal(result.documentParts.easy, notice.documentParts.easy);
});
test("사전 없음·대기·실패를 임의의 쉬운말로 대체하지 않는다", () => {
  for (const status of ["not_found", "pending", "failed"]) {
    const result = withNoticeDictionary(notice, payload(status));
    const term = result.documentParts.original.find((p) => p.term).term;
    assert.equal(term.dictionary.status, status);
    assert.deepEqual(term.dictionary.entries, []);
    assert.equal(term.plain, "");
  }
});
test("다른 공지·원문과 잘못된 위치·겹친 후보는 모두 연결하지 않는다", () => {
  for (const mutate of [
    (p) => (p.notice_id = 7),
    (p) => (p.original_text += "변경"),
    (p) => p.dictionary_candidates[0].start--,
    (p) => (p.dictionary_candidates[0].end = 999),
    (p) => (p.dictionary_candidates[0].start = 0),
    (p) => (p.dictionary_candidates[0].start += 0.5),
    (p) => p.dictionary_candidates.push(p.dictionary_candidates[0]),
    (p) => (p.dictionary_candidates[0].original = "다른말"),
  ]) {
    const value = payload();
    mutate(value);
    const result = withNoticeDictionary(notice, value);
    assert.equal(result.dictionaryStatus, "failed");
    assert.deepEqual(result.documentParts.original, [
      { text: notice.original },
    ]);
  }
});
test("비공식 링크·항목 불일치·잘못된 응답의 뜻풀이를 표시하지 않는다", () => {
  for (const mutate of [
    (p) =>
      (p.dictionary_candidates[0].dictionary.entries[0].source_url =
        "http://stdict.korean.go.kr/search/searchView.do?word_no=123"),
    (p) =>
      (p.dictionary_candidates[0].dictionary.entries[0].source_url =
        "https://example.test/?word_no=123"),
    (p) =>
      (p.dictionary_candidates[0].dictionary.entries[0].target_code = "999"),
    (p) => (p.dictionary_candidates[0].dictionary.query_word = "다른말"),
    (p) => (p.dictionary_candidates[0].dictionary.entries[0].senses = []),
  ]) {
    const value = payload();
    mutate(value);
    const term = withNoticeDictionary(
      notice,
      value,
    ).documentParts.original.find((p) => p.term).term;
    assert.equal(term.dictionary.status, "failed");
    assert.deepEqual(term.dictionary.entries, []);
  }
});
test("조회 실패와 미처리는 원문·쉬운말을 보존하되 이전 치환을 사전으로 표시하지 않는다", () => {
  for (const state of ["loading", "failed", "unprocessed"]) {
    const result = withNoticeDictionary(notice, null, state);
    assert.equal(result.original, notice.original);
    assert.equal(result.easy, notice.easy);
    assert.equal(result.documentParts.easy, notice.documentParts.easy);
    assert.deepEqual(result.documentParts.original, [
      { text: notice.original },
    ]);
  }
});
test("열려 있는 설명도 재조회 결과를 따르고 원문 변경 시 닫힌다", () => {
  const previous = withNoticeDictionary(notice, payload());
  const term = previous.documentParts.original.find((p) => p.term).term;
  const next = withNoticeDictionary(notice, payload("pending"));
  assert.equal(currentDictionaryTerm(next, term).dictionary.status, "pending");
  assert.equal(
    currentDictionaryTerm(
      withNoticeDictionary({ ...notice, original: "새 원문" }, payload()),
      term,
    ),
    null,
  );
});
test("사전 조회 대기·오류가 상세 조회의 성공 결과를 막지 않는다", () => {
  for (const response of [
    { isPending: true },
    { isError: true },
    { data: payload() },
  ]) {
    const configs = [];
    const query = {
      data: notice,
      isPending: false,
      isError: false,
      refetch: () => {},
    };
    const hooks = loadTs(
      path.join(__dirname, "../src/features/notices/hooks/useNotices.ts"),
      {
        "@tanstack/react-query": {
          useQuery: (config) => {
            configs.push(config);
            return configs.length === 1 ? query : response;
          },
        },
        "../store/noticeScopeStore": {},
      },
    );
    const result = hooks.useNotice("6");
    assert.equal(result.isError, false);
    assert.equal(result.isPending, false);
    assert.equal(result.data.original, notice.original);
    assert.equal(typeof result.refetch, "function");
    assert.equal(configs[1].enabled, true);
    assert.ok(configs[1].queryKey.includes(notice.original));
  }
});

test("사전은 통신 오류만 재시도하고 처리 중 결과만 주기적으로 갱신한다", async () => {
  const configs = [];
  const { NoticeRequestError } = require("../src/features/notices/domain/noticeError.ts");
  let detailCalls = 0;
  let dictionaryCalls = 0;
  const hooks = loadTs(
    path.join(__dirname, "../src/features/notices/hooks/useNotices.ts"),
    {
      "@tanstack/react-query": {
        useQuery: (config) => {
          configs.push(config);
          return configs.length === 1
            ? { data: notice, refetch: async () => { detailCalls++; return { data: notice }; } }
            : { data: payload(), refetch: async () => { dictionaryCalls++; } };
        },
      },
      "../domain/noticeError": { NoticeRequestError },
      "../store/noticeScopeStore": {},
    },
  );
  const result = hooks.useNotice("6");
  const config = configs[1];
  assert.equal(config.retry(0, new NoticeRequestError("connection")), true);
  assert.equal(config.retry(1, new NoticeRequestError("connection")), false);
  for (const code of ["configuration", "contract"])
    assert.equal(config.retry(0, new NoticeRequestError(code)), false);
  const interval = (data, status = "success") => config.refetchInterval({ state: { data, status } });
  assert.equal(interval(payload("pending")), 10_000);
  assert.equal(interval({ ...payload(), dictionary_status: "pending", dictionary_candidates: null }), 10_000);
  assert.equal(interval(payload()), false);
  assert.equal(interval(payload("not_found")), false);
  assert.equal(interval(null), false);
  assert.equal(interval(payload("pending"), "error"), false);
  assert.equal(config.refetchIntervalInBackground, false);
  const refreshed = await result.refetch();
  assert.equal(refreshed.data, notice);
  assert.equal(detailCalls, 1);
  assert.equal(dictionaryCalls, 1);
});

test("파이프라인 e2e가 기록한 실제 공개 사전 응답을 연결한다", () => {
  const fs = require("node:fs");
  const {
    parseNotice,
  } = require("../src/features/notices/api/noticeContract.ts");
  const snapshot = JSON.parse(
    fs.readFileSync(
      path.join(
        __dirname,
        "../../../services/pipeline/tests/e2e/cases/easy_text_dictionary_candidates/expected/step-9.json",
      ),
      "utf8",
    ),
  );
  for (const [key, raw] of Object.entries(snapshot.anon.app_notice_detail)) {
    // E2E snapshots key rows by source and omit the redundant database ID.
    const value = parseNotice({
      ...raw,
      id: snapshot.anon.get_notice_dictionary[key].result.notice_id,
    });
    const result = withNoticeDictionary(
      value,
      snapshot.anon.get_notice_dictionary[key].result,
    );
    assert.equal(result.dictionaryStatus, "complete");
    const terms = result.documentParts.original.filter((part) => part.term);
    assert.ok(terms.length >= 3);
    assert.ok(terms.some((part) => part.term.dictionary.status === "found"));
    assert.ok(
      terms.some((part) => part.term.dictionary.status === "not_found"),
    );
    assert.equal(
      result.documentParts.original.map((part) => part.text).join(""),
      value.original,
    );
  }
});

test("기존 팝업에서 원문은 실제 사전 뜻과 출처, 쉬운말은 원문 표현을 표시한다", () => {
  const fs = require("node:fs");
  const ts = require("typescript");
  const element = (type, props) => ({ type, props });
  const component = { exports: {} };
  const compiled = ts.transpileModule(
    fs.readFileSync(
      path.join(
        __dirname,
        "../src/features/notices/components/NoticeTermOverlay.tsx",
      ),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        target: ts.ScriptTarget.ES2022,
      },
    },
  ).outputText;
  const mocks = {
    react: { useState: (value) => [value, () => {}], useEffect: () => {} },
    "react/jsx-runtime": { jsx: element, jsxs: element, Fragment: "Fragment" },
    "react-native": {
      Modal: "Modal",
      View: "View",
      ScrollView: "ScrollView",
      Pressable: "Pressable",
      StyleSheet: { create: (value) => value },
      useWindowDimensions: () => ({ height: 800 }),
      Animated: { Value: class {}, View: "AnimatedView" },
    },
    "expo-image": { Image: "Image" },
    "react-native-safe-area-context": {
      useSafeAreaInsets: () => ({ top: 0, bottom: 0 }),
    },
    "@/shared/ui/AppText": { AppText: "AppText" },
    "@/shared/theme/tokens": { COLORS: {}, CARD_SHADOW: {} },
  };
  new Function("module", "exports", "require", compiled)(
    component,
    component.exports,
    (name) => mocks[name] ?? "asset",
  );
  const term = withNoticeDictionary(
    notice,
    payload(),
  ).documentParts.original.find((p) => p.term).term;
  function text(node) {
    if (Array.isArray(node)) return node.map(text).join(" ");
    if (node && typeof node === "object") return text(node.props?.children);
    return typeof node === "string" ? node : "";
  }
  for (const comfortable of [false, true]) {
    const tree = component.exports.NoticeTermOverlay({
      term,
      easy: false,
      comfortable,
      onClose() {},
    });
    assert.match(text(tree), /물건을 가지고 가거나 옴/);
    assert.match(text(tree), /다른 뜻풀이/);
    assert.match(text(tree), /국립국어원 표준국어대사전/);
    assert.doesNotMatch(text(tree), /쉬운말 표현/);
    const easy = component.exports.NoticeTermOverlay({
      term: { original: "지참", plain: "가져오기" },
      easy: true,
      comfortable,
      onClose() {},
    });
    assert.match(text(easy), /원문 단어/);
    assert.match(text(easy), /가져오기/);
    assert.doesNotMatch(text(easy), /표준국어대사전/);
  }
});

test("쉬운말이 무효화되면 같은 본문의 이전 사전 캐시도 표시하지 않는다", () => {
  const result = withNoticeDictionary(
    { ...notice, hasEasyText: false },
    payload(),
    "unprocessed",
  );
  assert.deepEqual(result.documentParts.original, [{ text: notice.original }]);
  assert.equal(result.dictionaryStatus, "unprocessed");
});
