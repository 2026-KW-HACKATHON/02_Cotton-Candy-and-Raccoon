const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

// 화면을 실행하지 않고 재조회 전후의 본문·버튼·단어 설명 연결을 검증한다.
function loadDetail(initialNotice, standard = false) {
  let notice = initialNotice;
  let cursor = 0;
  let updated = false;
  const slots = [];
  const effects = [];
  const react = {
    useCallback: (fn) => fn,
    useEffect: (fn) => effects.push(fn),
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [
        slots[index],
        (value) => {
          const next =
            typeof value === "function" ? value(slots[index]) : value;
          if (!Object.is(next, slots[index])) updated = true;
          slots[index] = next;
        },
      ];
    },
  };
  const element = (type, props, key) => ({ type, props, key });
  let previousKey;
  const mocks = {
    react,
    "react/jsx-runtime": { jsx: element, jsxs: element, Fragment: "Fragment" },
    "react-native": {
      View: "View",
      Pressable: "Pressable",
      StyleSheet: { create: (styles) => styles },
    },
    "expo-image": { Image: "Image" },
    "expo-router": {
      useLocalSearchParams: () => ({ id: "6" }),
      useFocusEffect() {},
    },
    "@/shared/theme/tokens": {
      COLORS: {},
      EASY: {},
      RADIUS: {},
      CARD_SHADOW: {},
    },
    "../hooks/useNotices": {
      useNotice: () => ({ data: notice, isError: false, isPending: false }),
    },
    "../store/bookmarkStore": {
      useBookmarkStore: (select) =>
        select({ savedIds: [], toggleBookmark() {} }),
    },
    "../domain/noticePresentation": {
      getSummaryRows: () => [],
      isNoticeExpired: () => false,
    },
  };
  for (const component of [
    "Screen",
    "Header",
    "AppText",
    "EasyButton",
    "IconButton",
  ]) {
    mocks[`@/shared/ui/${component}`] = { [component]: component, goBack() {} };
  }
  for (const component of [
    "CategoryBadge",
    "NoticeState",
    "EasyNoticeState",
    "NoticeDocumentText",
    "NoticeTermOverlay",
    "NoticeFiles",
    "NoticeSummaryStatus",
    "NoticeEvidence",
  ]) {
    mocks[`../components/${component}`] = { [component]: component };
  }
  mocks["lucide-react-native"] = { Bookmark: "Bookmark" };
  mocks["@/shared/accessibility/displayPreferences"] = {
    useDisplayPreferences: (select) => select({ mode: "standard" }),
  };
  mocks["./EasyNoticeDetailScreen"] = {
    EasyNoticeDetailScreen: "EasyNoticeDetailScreen",
  };
  mocks["@/shared/ui/character/AnimatedCharacter"] = {
    DetailCharacter: "DetailCharacter",
  };
  const source = fs.readFileSync(
    path.join(
      __dirname,
      `../src/features/notices/screens/${standard ? "NoticeDetailScreen" : "EasyNoticeDetailScreen"}.tsx`,
    ),
    "utf8",
  );
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
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
      if (standard) {
        const content = loaded.exports.NoticeDetailScreen().type();
        if (content.key !== previousKey) {
          slots.length = 0;
          previousKey = content.key;
        }
        tree = content.type(content.props);
      } else tree = loaded.exports.EasyNoticeDetailScreen();
      while (effects.length) effects.shift()();
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
      evidence: nodes.find((node) => node.type === "NoticeEvidence").props,
      document: nodes.find((node) => node.type === "NoticeDocumentText").props,
      toggle: standard
        ? (() => {
            const button = nodes.find(
              (node) =>
                node.type === "Pressable" &&
                node.props.accessibilityLabel === "쉬운말",
            ).props;
            const easy = nodes.find(
              (node) => node.type === "NoticeDocumentText",
            ).props.easy;
            return {
              ...button,
              label: easy ? "원문으로 읽기" : "쉬운말로 읽기",
              onPress: easy
                ? nodes.find(
                    (node) =>
                      node.type === "Pressable" &&
                      node.props.accessibilityLabel === "원문",
                  ).props.onPress
                : button.onPress,
            };
          })()
        : nodes.find(
            (node) =>
              node.type === "EasyButton" && node.props.label.includes("읽기"),
          ).props,
      overlay: nodes.find((node) => node.type === "NoticeTermOverlay").props,
    };
  }
  return {
    render,
    update(value) {
      notice = value;
    },
  };
}

const NOTICE = {
  id: "6",
  title: "공문",
  publishedAt: "2026.10.09",
  provider: "노원구청",
  original: "원문 본문",
  easy: "쉬운말 본문",
  hasEasyText: true,
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
    ...NOTICE,
    original: "갱신된 원문",
    easy: "",
    hasEasyText: false,
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

for (const standard of [true]) {
  test("일반 상세도 쉬운말 무효화 시 원문과 선택 상태를 복구한다", () => {
    const screen = loadDetail(NOTICE, standard);
    screen.render().toggle.onPress();
    assert.equal(screen.render().document.text, NOTICE.easy);
    screen.render().document.onTermPress({ original: "단어", plain: "표현" });
    screen.update({
      ...NOTICE,
      original: "새 원문",
      easy: "",
      hasEasyText: false,
    });
    const state = screen.render();
    assert.equal(state.document.text, "새 원문");
    assert.equal(state.overlay.term, null);
    assert.equal(state.toggle.disabled, true);
    screen.update(NOTICE);
    assert.equal(screen.render().document.easy, false);
  });
}

test("일반·편한 상세 모두 근거와 누락 첨부 정보를 표시 컴포넌트에 전달한다", () => {
  const notice = {
    ...NOTICE,
    evidence: [{ quote: "원문", label: "본문 근거" }],
    omissions: [{ message: "미지원 첨부", url: "https://example.test/file" }],
  };
  for (const standard of [false, true]) {
    const state = loadDetail(notice, standard).render();
    assert.equal(state.evidence.notice, notice);
    assert.equal(!!state.evidence.comfortable, !standard);
  }
});
