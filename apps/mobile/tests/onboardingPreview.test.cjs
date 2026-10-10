const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
require("./loadTypeScript.cjs");
const {
  EASY_ONBOARDING,
  STANDARD_ONBOARDING,
} = require("../src/features/settings/domain/onboardingSteps.ts");
const {
  NOTICE_PREVIEW_FIXTURES,
} = require("../src/features/notices/fixtures/noticeFixtures.ts");
const {
  splitGlossaryText,
} = require("../src/features/notices/domain/noticePresentation.ts");

const element = (type, props) => ({ type, props });
const common = {
  react: {
    useRef: () => ({ current: null }),
    useEffect() {},
    useState: (value) => [value, () => {}],
  },
  "react/jsx-runtime": { jsx: element, jsxs: element, Fragment: "Fragment" },
  "react-native": {
    View: "View",
    Pressable: "Pressable",
    ScrollView: "ScrollView",
    StyleSheet: { create: (value) => value },
    Platform: { OS: "web" },
    useWindowDimensions: () => ({ width: 390 }),
  },
  "expo-image": { Image: "Image" },
  "lucide-react-native": Object.fromEntries(
    [
      "Bookmark",
      "ChevronDown",
      "Settings",
      "X",
      "ChevronLeft",
      "ChevronRight",
    ].map((name) => [name, name]),
  ),
  "@/shared/ui/AppText": { AppText: "AppText" },
  "@/shared/theme/tokens": { COLORS: {} },
};

function load(relative, mocks = {}) {
  const source = fs.readFileSync(
    path.join(__dirname, "../src", relative),
    "utf8",
  );
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const loaded = { exports: {} };
  const dependencies = { ...common, ...mocks };
  new Function("exports", "require", compiled)(loaded.exports, (name) => {
    if (name.startsWith("@/assets/")) return "asset";
    assert.ok(name in dependencies, `Unexpected import: ${name}`);
    return dependencies[name];
  });
  return loaded.exports;
}

function fixture() {
  const moves = [];
  const input = { wheel: false, pan: false };
  const gesture = new Proxy(
    {},
    {
      get:
        (_target, key) =>
        (...args) => {
          if (key === "enabled") input.pan = args[0];
          return gesture;
        },
    },
  );
  const orbit = {
    displayNotices: NOTICE_PREVIEW_FIXTURES.slice(0, 3),
    center: 0,
    busy: false,
    clock: {},
    position: {},
    dragInput: { set() {} },
    beginTouch() {},
    beginDrag() {},
    endDrag() {},
    move: (direction) => moves.push(direction),
    click: (direction) => moves.push(direction),
  };
  const { LetterOrbit } = load("features/notices/components/LetterOrbit.tsx", {
    "react-native-gesture-handler": {
      Gesture: { Pan: () => gesture },
      GestureDetector: "GestureDetector",
    },
    "react-native-worklets": { scheduleOnRN() {} },
    "react-native-reanimated": {
      default: { View: "AnimatedView" },
      useAnimatedStyle: () => ({}),
    },
    "@/shared/ui/IconButton": {
      IconButton: (props) => element("Pressable", props),
    },
    "../hooks/useLetterOrbit": { useLetterOrbit: () => orbit },
    "../hooks/useLetterOrbitWheel": {
      useLetterOrbitWheel: (options) => {
        input.wheel = options.enabled;
        return null;
      },
    },
    "../domain/letterOrbit": {
      getOrbitPose() {},
      getOrbitSlots: () => [0],
      wrapNoticeIndex: (index) => index,
    },
    "../domain/letterOrbitInput": { getEdgeTouchWidth: () => 48 },
    "./LetterIllustration": {
      LetterIllustration: "LetterIllustration",
      ClosedEnvelope: "ClosedEnvelope",
      LETTER_HEIGHT: 504,
      LETTER_WIDTH: 290,
    },
  });
  const { NoticeDocumentText } = load(
    "features/notices/components/NoticeDocumentText.tsx",
    {
      "../domain/noticePresentation": { splitGlossaryText },
      "../domain/summaryEvidence": require("./support/loadTs.cjs").loadTs(path.join(__dirname, "../src/features/notices/domain/summaryEvidence.ts")),
    },
  );
  const preview = load("features/settings/components/OnboardingPreview.tsx", {
    "@/features/notices/components/LetterOrbit": { LetterOrbit },
    "@/features/notices/components/NoticeDocumentText": { NoticeDocumentText },
    "@/features/notices/fixtures/noticeFixtures": { NOTICE_PREVIEW_FIXTURES },
  });
  return { ...preview, LetterOrbit, NoticeDocumentText, moves, input };
}

function nodes(tree) {
  const result = [];
  function visit(node, hidden = false) {
    if (Array.isArray(node))
      return node.forEach((child) => visit(child, hidden));
    if (!node || typeof node !== "object") return;
    if (typeof node.type === "function")
      return visit(node.type(node.props), hidden);
    const blocked =
      hidden ||
      node.props?.["aria-hidden"] === true ||
      node.props?.pointerEvents === "none";
    result.push({ ...node, hidden: blocked });
    visit(node.props?.children, blocked);
  }
  visit(tree);
  return result;
}

test("일반·편한 화면의 모든 단계에서 숨겨진 버튼은 입력과 포커스를 차단한다", () => {
  for (const [mode, steps] of [
    ["standard", STANDARD_ONBOARDING],
    ["easy", EASY_ONBOARDING],
  ]) {
    for (const step of [undefined, ...steps]) {
      const { OnboardingPreview } = fixture();
      const rendered = nodes(OnboardingPreview({ mode, step, onTry() {} }));
      for (const node of rendered.filter(
        (node) => node.type === "Pressable" && node.hidden,
      )) {
        assert.equal(
          node.props.disabled,
          true,
          `${mode}/${step?.target}: hidden button enabled`,
        );
        assert.equal(node.props.onPress, undefined);
      }
      for (const node of rendered.filter(
        (node) => node.props?.tabIndex !== undefined && node.hidden,
      )) {
        assert.equal(node.props.tabIndex, -1);
      }
    }
  }
});

test("공문 넘기기 안내에서 실제 캐러셀의 화살표와 방향키를 사용할 수 있다", () => {
  const { OnboardingPreview, moves, input } = fixture();
  const step = STANDARD_ONBOARDING.find((item) => item.target === "carousel");
  const rendered = nodes(
    OnboardingPreview({ mode: "standard", step, onTry() {} }),
  );
  const arrows = rendered.filter((node) =>
    ["이전 공문", "다음 공문"].includes(node.props?.accessibilityLabel),
  );
  assert.equal(arrows.length, 2);
  for (const arrow of arrows) {
    assert.equal(arrow.hidden, false);
    assert.equal(arrow.props.disabled, false);
    arrow.props.onPress();
  }
  assert.deepEqual(moves, [-1, 1]);
  const root = rendered.find((node) => node.props?.tabIndex === 0);
  const target = {};
  root.props.onKeyDown({
    key: "ArrowRight",
    target,
    currentTarget: target,
    preventDefault() {},
  });
  assert.deepEqual(moves, [-1, 1, 1]);
  assert.equal(input.wheel, true);
  assert.equal(input.pan, true);
});

test("비활성 캐러셀은 방향키·휠·드래그와 상세 이동을 차단한다", () => {
  const { LetterOrbit, moves, input } = fixture();
  const rendered = nodes(
    LetterOrbit({
      notices: NOTICE_PREVIEW_FIXTURES.slice(0, 3),
      interactive: false,
      onOpen: () => assert.fail("inactive open"),
    }),
  );
  const root = rendered.find((node) => node.props?.tabIndex === -1);
  const target = {};
  root.props.onKeyDown({
    key: "ArrowRight",
    target,
    currentTarget: target,
    preventDefault() {},
  });
  rendered.find((node) => node.type === "LetterIllustration").props.onOpen();
  assert.deepEqual(moves, []);
  assert.equal(input.wheel, false);
  assert.equal(input.pan, false);
});

test("하단 안내 이동 버튼과 제품 캐러셀의 기본 조작은 유지한다", () => {
  const { PreviewButton, LetterOrbit, moves } = fixture();
  let pressed = false;
  const button = PreviewButton({
    label: "다음",
    onPress: () => {
      pressed = true;
    },
  });
  assert.equal(button.props.disabled, false);
  button.props.onPress();
  assert.equal(pressed, true);
  const rendered = nodes(
    LetterOrbit({ notices: NOTICE_PREVIEW_FIXTURES.slice(0, 3), onOpen() {} }),
  );
  rendered
    .find((node) => node.props?.accessibilityLabel === "다음 공문")
    .props.onPress();
  assert.deepEqual(moves, [1]);
});

test("원문 근거 배경과 사전 밑줄·클릭은 함께 동작하며 쉬운말은 강조하지 않는다", () => {
  const { NoticeDocumentText } = fixture();
  const term = { original: "공시송달", plain: "게시판 알림", meaning: "뜻" };
  let pressed;
  const props = {
    text: "공시송달 신청", parts: [{ text: "공시송달", term }, { text: " 신청" }],
    highlights: [{ start: 0, end: 4 }], onTermPress: (value) => { pressed = value; },
  };
  for (const comfortable of [false, true]) {
    const rendered = nodes(NoticeDocumentText({ ...props, comfortable, easy: false }));
    const button = rendered.find((n) => n.type === "Pressable");
    button.props.onPress();
    assert.equal(pressed, term);
    const hasHighlight = (n) => JSON.stringify(n.props?.style ?? null).includes("#FFF2A8");
    assert.ok(rendered.some(hasHighlight));
    const easy = nodes(NoticeDocumentText({ ...props, comfortable, easy: true }));
    assert.ok(!easy.some(hasHighlight));
  }
});
