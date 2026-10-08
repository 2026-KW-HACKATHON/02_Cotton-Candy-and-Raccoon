const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const babel = require("@babel/core");
const React = require("react");
const root = path.resolve(__dirname, "..");
const feature = path.join(root, "src/features/widget");
const library = path.join(root, "node_modules/react-native-android-widget/src");

function loader(overrides = {}) {
  const cache = new Map();
  function load(file, compile = false) {
    if (!fs.existsSync(file))
      file += fs.existsSync(file + ".ts") ? ".ts" : ".tsx";
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} };
    cache.set(file, module);
    const code = compile
      ? babel.transformFileSync(file, {
          presets: [["babel-preset-expo", { platform: "android" }]],
          plugins: [
            "babel-plugin-react-compiler",
            "@babel/plugin-transform-modules-commonjs",
          ],
          configFile: false,
          babelrc: false,
        }).code
      : ts.transpileModule(fs.readFileSync(file, "utf8"), {
          compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            jsx: ts.JsxEmit.ReactJSX,
            target: ts.ScriptTarget.ES2022,
          },
        }).outputText;
    new Function("require", "module", "exports", code)(
      (name) => {
        if (name in overrides) return overrides[name];
        if (name === "react-native")
          return { Image: { resolveAssetSource: () => ({ uri: "" }) } };
        if (name.startsWith("."))
          return load(path.resolve(path.dirname(file), name));
        return require(name);
      },
      module,
      module.exports,
    );
    return module.exports;
  }
  return load;
}

test("React Compiler 이후 실제 위젯 트리 생성과 루트 접근성 라벨을 검증한다", () => {
  const native = loader();
  const primitives = Object.assign(
    {},
    ...["FlexWidget", "OverlapWidget", "SvgWidget", "TextWidget"].map((name) =>
      native(path.join(library, "widgets", name)),
    ),
  );
  const load = loader({ "react-native-android-widget": primitives });
  const { TodayNoticeWidget } = load(
    path.join(feature, "TodayNoticeWidget.tsx"),
    true,
  );
  const { buildWidgetTree } = native(
    path.join(library, "api/build-widget-tree.ts"),
  );
  for (const status of ["ready", "empty", "error", "loading"]) {
    const tree = buildWidgetTree(
      React.createElement(TodayNoticeWidget, {
        width: 250,
        height: 370,
        state: {
          status,
          date: "2026-10-09",
          notice: { title: "오늘 공문", provider: "월계1동" },
        },
      }),
    );
    assert.match(tree.props.accessibilityLabel, /2026-10-09/);
    if (status === "ready")
      assert.match(tree.props.accessibilityLabel, /오늘 공문.*월계1동/);
    assert.equal(tree.type, "LinearLayoutWidget");
  }
});

function harness() {
  const pending = [];
  const load = loader({
    "./TodayNoticeWidget": { TodayNoticeWidget: () => null },
    "./todayNotice": {
      koreaDate: () => "2026-10-09",
      loadTodayNotice: () => new Promise((resolve) => pending.push(resolve)),
    },
  });
  const { widgetTaskHandler } = load(
    path.join(feature, "widgetTaskHandler.tsx"),
  );
  const renders = [];
  const run = (id, action = "WIDGET_UPDATE", width = 250) =>
    widgetTaskHandler({
      widgetInfo: {
        widgetName: "TodayNotice",
        widgetId: id,
        width,
        height: 370,
      },
      widgetAction: action,
      clickAction: "REFRESH",
      renderWidget: (element) => renders.push({ id, ...element.props }),
    });
  return { pending, renders, run };
}

test("늦게 끝난 이전 요청은 최신 결과와 크기를 덮지 않는다", async () => {
  const h = harness();
  const old = h.run(1);
  const latest = h.run(1, "WIDGET_RESIZED", 300);
  h.pending[1]({ status: "ready", date: "2026-10-09" });
  await latest;
  h.pending[0]({ status: "error", date: "2026-10-09" });
  await old;
  assert.equal(h.renders.length, 3);
  assert.equal(h.renders.at(-1).state.status, "ready");
  assert.equal(h.renders.at(-1).width, 300);
});

test("서로 다른 위젯 갱신은 독립적으로 완료된다", async () => {
  const h = harness();
  const first = h.run(1);
  const second = h.run(2);
  h.pending[1]({ status: "empty" });
  h.pending[0]({ status: "ready" });
  await Promise.all([first, second]);
  assert.deepEqual(
    h.renders
      .filter((x) => x.state.status !== "loading")
      .map((x) => x.id)
      .sort(),
    [1, 2],
  );
});

test("삭제 후 같은 ID를 재사용해도 이전 요청은 반영되지 않는다", async () => {
  const h = harness();
  const old = h.run(1);
  await h.run(1, "WIDGET_DELETED");
  const added = h.run(1, "WIDGET_ADDED");
  h.pending[0]({ status: "ready" });
  await old;
  assert.equal(h.renders.length, 2);
  h.pending[1]({ status: "empty" });
  await added;
  assert.equal(h.renders.at(-1).state.status, "empty");
});
