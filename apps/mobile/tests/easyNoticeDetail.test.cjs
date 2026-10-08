const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

// 화면을 실행하지 않고 재조회 전후의 본문·버튼·단어 설명 연결을 검증한다.
function loadDetail(initialNotice) {
  let notice = initialNotice;
  let cursor = 0;
  let updated = false;
  const slots = [];
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => {
        const next = typeof value === "function" ? value(slots[index]) : value;
        if (!Object.is(next, slots[index])) updated = true;
        slots[index] = next;
      }];
    },
  };
  const element = (type, props) => ({ type, props });
  const mocks = {
    react,
    "react/jsx-runtime": { jsx: element, jsxs: element, Fragment: "Fragment" },
    "react-native": { View: "View", StyleSheet: { create: (styles) => styles } },
    "expo-image": { Image: "Image" },
    "expo-router": { useLocalSearchParams: () => ({ id: "6" }) },
    "@/shared/theme/tokens": { COLORS: {}, EASY: {} },
    "../hooks/useNotices": {
      useNotice: () => ({ data: notice, isError: false, isPending: false }),
    },
    "../store/bookmarkStore": {
      useBookmarkStore: (select) => select({ savedIds: [], toggleBookmark() {} }),
    },
    "../domain/noticePresentation": {
      getSummaryRows: () => [],
      isNoticeExpired: () => false,
    },
  };
  for (const component of ["Screen", "Header", "AppText", "EasyButton"]) {
    mocks[`@/shared/ui/${component}`] = { [component]: component, goBack() {} };
  }
  for (const component of [
    "EasyNoticeState", "NoticeDocumentText", "NoticeTermOverlay", "NoticeFiles", "NoticeSummaryStatus",
  ]) {
    mocks[`../components/${component}`] = { [component]: component };
  }
  const source = fs.readFileSync(path.join(
    __dirname, "../src/features/notices/screens/EasyNoticeDetailScreen.tsx",
  ), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const loaded = { exports: {} };
  new Function("exports", "require", compiled)(loaded.exports, (name) => {
    if (name.startsWith("@/assets/")) return "asset";
    assert.ok(name in mocks, `Unexpected import: ${name}`);
    return mocks[name];
  });
  function render() {
    let tree;
    let attempts = 0;
    do {
      assert.ok(attempts++ < 10, "Too many re-renders");
      cursor = 0;
      updated = false;
      tree = loaded.exports.EasyNoticeDetailScreen();
    } while (updated);
    const nodes = [];
    function visit(node) {
      if (Array.isArray(node)) return node.forEach(visit);
      if (!node || typeof node !== "object") return;
      nodes.push(node);
      visit(node.props?.children);
      visit(node.props?.overlay);
      visit(node.props?.header);
    }
    visit(tree);
    return {
      document: nodes.find((node) => node.type === "NoticeDocumentText").props,
      toggle: nodes.find((node) => node.type === "EasyButton" && node.props.label.includes("읽기")).props,
      overlay: nodes.find((node) => node.type === "NoticeTermOverlay").props,
    };
  }
  return {
    render,
    update(value) { notice = value; },
  };
}

const NOTICE = {
  id: "6", title: "공문", publishedAt: "2026.10.09", provider: "노원구청",
  original: "원문 본문", easy: "쉬운말 본문", hasEasyText: true,
  documentParts: {
    original: [{ text: "원문 본문" }],
    easy: [{ text: "쉬운말 본문" }],
  },
};

test("쉬운말과 원문을 오가며 현재 본문과 버튼 상태를 함께 바꾼다", () => {
  const screen = loadDetail(NOTICE);
  const original = screen.render();
  assert.equal(original.document.text, NOTICE.original);
  assert.equal(original.toggle.disabled, false);
  original.toggle.onPress();
  const easy = screen.render();
  assert.equal(easy.document.text, NOTICE.easy);
  assert.equal(easy.toggle.label, "원문으로 읽기");
  assert.equal(easy.toggle.disabled, false);
  easy.toggle.onPress();
  assert.equal(screen.render().document.text, NOTICE.original);
});

test("쉬운말 무효화 시 즉시 원문을 표시하고 이전 단어 설명과 선택을 해제한다", () => {
  const screen = loadDetail(NOTICE);
  screen.render().toggle.onPress();
  const easy = screen.render();
  easy.document.onTermPress({ original: "단어", plain: "표현" });
  assert.ok(screen.render().overlay.term);
  const originalParts = [{ text: "갱신된 원문" }];
  screen.update({
    ...NOTICE, original: "갱신된 원문", easy: "", hasEasyText: false,
    documentParts: { original: originalParts },
  });
  const invalidated = screen.render();
  assert.equal(invalidated.document.text, "갱신된 원문");
  assert.equal(invalidated.document.parts, originalParts);
  assert.equal(invalidated.document.easy, false);
  assert.equal(invalidated.toggle.label, "쉬운말로 읽기");
  assert.equal(invalidated.toggle.disabled, true);
  assert.equal(invalidated.overlay.term, null);
  screen.update(NOTICE);
  const regenerated = screen.render();
  assert.equal(regenerated.document.text, NOTICE.original);
  assert.equal(regenerated.toggle.disabled, false);
});

test("처음부터 쉬운말이 없는 공문도 원문을 표시한다", () => {
  const screen = loadDetail({ ...NOTICE, hasEasyText: false, easy: "" });
  const state = screen.render();
  assert.equal(state.document.text, NOTICE.original);
  assert.equal(state.toggle.disabled, true);
});
