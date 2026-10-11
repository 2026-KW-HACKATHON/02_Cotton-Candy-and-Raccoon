const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

function loadTitle(platform = "android") {
  let cursor = 0;
  let fontScale = 1;
  let systemFontScale = 1;
  const state = [];
  const element = (type, props, key) => ({ type, props, key });
  const mocks = {
    react: {
      useState(initial) {
        const index = cursor++;
        if (!(index in state)) state[index] = initial;
        return [
          state[index],
          (value) => {
            state[index] =
              typeof value === "function" ? value(state[index]) : value;
          },
        ];
      },
    },
    "react/jsx-runtime": { jsx: element, jsxs: element },
    "react-native": {
      Platform: { OS: platform },
      StyleSheet: { create: (value) => value },
      View: "View",
      useWindowDimensions: () => ({ fontScale: systemFontScale }),
    },
    "@/shared/accessibility/displayPreferences": {
      useDisplayPreferences: (select) => select({ fontScale }),
    },
    "@/shared/ui/AppText": { AppText: "AppText" },
  };
  const file = path.join(
    __dirname,
    "../src/features/notices/components/NoticeDetailTitle.tsx",
  );
  const compiled = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const loaded = { exports: {} };
  new Function("exports", "require", compiled)(loaded.exports, (name) => {
    assert.ok(name in mocks, `Unexpected import: ${name}`);
    return mocks[name];
  });
  return {
    render(title = "공문 제목") {
      cursor = 0;
      const tree = loaded.exports.NoticeDetailTitle({ title });
      const [probe, visible] = tree.props.children;
      return { tree, probe, visible };
    },
    setScale(app, system = 1) {
      fontScale = app;
      systemFontScale = system;
    },
  };
}

function measureLines(node, count) {
  node.props.onTextLayout({ nativeEvent: { lines: Array(count).fill({}) } });
}

test("기본 크기 3줄까지 유지하고 4줄부터 한 단계만 축소한다", () => {
  const title = loadTitle();
  measureLines(title.render().probe, 3);
  assert.equal(title.render().visible.props.size, 28);
  measureLines(title.render().probe, 4);
  assert.equal(title.render().visible.props.size, 24);
  assert.equal(title.render().visible.props.lineHeight, 33);
  assert.equal(title.render().probe.props.size, 28);
  measureLines(title.render().probe, 12);
  assert.equal(title.render().visible.props.size, 24);
  assert.equal(title.render().visible.props.children, "공문 제목");
});

test("너비·제목·앱 및 OS 글자 배율이 바뀌면 기본 크기로 다시 판단한다", () => {
  const title = loadTitle();
  measureLines(title.render().probe, 4);
  const previousKey = title.render().probe.key;
  title
    .render()
    .tree.props.onLayout({ nativeEvent: { layout: { width: 320 } } });
  assert.notEqual(title.render().probe.key, previousKey);
  assert.equal(title.render().visible.props.size, 28);
  measureLines(title.render().probe, 3);
  assert.equal(title.render().visible.props.size, 28);
  measureLines(title.render().probe, 4);
  assert.equal(title.render("다른 공문 제목").visible.props.size, 28);
  title.setScale(1.15);
  assert.equal(title.render().visible.props.size, 28);
  measureLines(title.render().probe, 4);
  title.setScale(1.15, 1.5);
  assert.equal(title.render().visible.props.size, 28);
});

test("웹에서는 확대된 기본 줄 높이로 측정하고 측정용 제목은 접근성에서 제외한다", () => {
  const title = loadTitle("web");
  title.setScale(1.15);
  let node = title.render().probe;
  assert.equal(node.props.onTextLayout, undefined);
  assert.equal(node.props["aria-hidden"], true);
  assert.equal(node.props.importantForAccessibility, "no-hide-descendants");
  node.props.onLayout({ nativeEvent: { layout: { height: 38 * 1.15 * 3 } } });
  assert.equal(title.render().visible.props.size, 28);
  node = title.render().probe;
  node.props.onLayout({ nativeEvent: { layout: { height: 38 * 1.15 * 4 } } });
  assert.equal(title.render().visible.props.size, 24);
});
