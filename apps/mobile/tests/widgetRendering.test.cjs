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
const assetPaths = [];

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
          cwd: root,
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
          return {
            Image: {
              resolveAssetSource: (id) => ({
                uri: `asset:///${path.basename(assetPaths[id - 1])}`,
              }),
            },
          };
        if (name.startsWith("@/assets/")) {
          assert.ok(
            fs.existsSync(path.join(root, name.slice(2))),
            `Missing asset: ${name}`,
          );
          let index = assetPaths.indexOf(name);
          if (index < 0) index = assetPaths.push(name) - 1;
          return index + 1;
        }
        if (name.startsWith("."))
          return load(path.resolve(path.dirname(file), name));
        if (name.startsWith("@/"))
          return load(path.join(root, "src", name.slice(2)));
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
  const { NoticeCardWidget } = load(
    path.join(feature, "NoticeCardWidget.tsx"),
    true,
  );
  const { buildWidgetTree } = native(
    path.join(library, "api/build-widget-tree.ts"),
  );
  for (const Widget of [TodayNoticeWidget, NoticeCardWidget]) {
    for (const [width, height] of [
      [270, 60],
      [350, 80],
      [350, 110],
      [250, 110],
      [350, 220],
      [600, 200],
    ]) {
      for (const status of ["ready", "empty", "error", "loading"]) {
        const tree = buildWidgetTree(
          React.createElement(Widget, {
            width,
            height,
            state: {
              status,
              date: "2026-10-09",
              notice: {
                id: "42/a?b#c",
                title:
                  "오늘 공문 제목이 길어지는 경우에도 제공처와 등록일 영역을 침범하지 않습니다",
                provider: "월계1동",
                category: "주민 참여",
              },
            },
          }),
        );
        const label =
          Widget === NoticeCardWidget
            ? tree.children[0].children[0].props.accessibilityLabel
            : tree.props.accessibilityLabel;
        if (status === "ready") {
          assert.match(label, /2026-10-09 등록/);
          assert.match(label, /오늘 공문.*월계1동/);
        } else {
          assert.match(label, /최근 공문/);
          assert.doesNotMatch(label, /2026-10-09 등록/);
        }
        assert.equal(tree.type, "LinearLayoutWidget");
        if (Widget === NoticeCardWidget) {
          const canvas = tree.children[0];
          const card = canvas.children[0];
          assert.equal(
            card.props.clickAction,
            status === "ready" ? "OPEN_URI" : "OPEN_APP",
          );
          if (status === "ready") {
            assert.equal(
              card.props.clickActionData.uri,
              "mobile:///notice/42%2Fa%3Fb%23c",
            );
          } else assert.deepEqual(card.props.clickActionData, {});
          const refresh = canvas.children.find(
            (child) => child.props.clickAction === "REFRESH",
          );
          const character = card.children.find(
            (child) => child.props.svgUrl === "asset:///character-source.svg",
          );
          assert.equal(refresh.props.accessibilityLabel, "최근 공문 새로고침");
          assert.ok(refresh.props.width >= 48 && refresh.props.height >= 48);
          assert.ok(
            refresh.props.margin.top + refresh.props.height <=
              card.props.margin.top + character.props.margin.top,
          );
          assert.equal(refresh.props.margin.top, 0);
          assert.equal(refresh.props.margin.left + refresh.props.width, width);
          assert.equal(
            refresh.children[0].children[0].props.svgUrl,
            "asset:///refresh.svg",
          );
          for (const name of [
            "list-imgPostageStampPerforatedBorder.svg",
            "list-imgLeftStripe1.svg",
            "list-imgLeftStripe2.svg",
          ]) {
            const nodes = card.children.flatMap((child) => [
              child,
              ...(child.children ?? []),
            ]);
            assert.ok(
              nodes.some((child) => child.props.svgUrl === `asset:///${name}`),
            );
          }
          assert.ok(
            card.children.some(
              (child) => child.props.svgUrl === "asset:///character-source.svg",
            ),
          );
          if (status === "ready") {
            const title = card.children.find((child) =>
              child.props.text?.startsWith("오늘 공문"),
            );
            const provider = card.children.find(
              (child) => child.props.text === "월계1동",
            );
            const date = card.children.find(
              (child) => child.props.text === "2026. 10. 09",
            );
            assert.ok(title.props.width > 0);
            assert.ok(
              title.props.margin.top + title.props.height <=
                provider.props.margin.top,
            );
            assert.equal(title.props.truncate, "END");
            assert.equal(provider.props.truncate, "END");
            assert.equal(provider.props.margin.top, date.props.margin.top);
            assert.ok(
              provider.props.margin.left + provider.props.width <=
                date.props.margin.left,
            );
          }
        }
      }
    }
  }
});

function harness() {
  const pending = [];
  const load = loader({
    "./TodayNoticeWidget": { TodayNoticeWidget: () => null },
    "./NoticeCardWidget": { NoticeCardWidget: () => null },
    "./latestNotice": {
      koreaDate: () => "2026-10-09",
      loadLatestNotice: () => new Promise((resolve) => pending.push(resolve)),
    },
  });
  const { widgetTaskHandler } = load(
    path.join(feature, "widgetTaskHandler.tsx"),
  );
  const renders = [];
  const run = (
    id,
    action = "WIDGET_UPDATE",
    width = 250,
    widgetName = "TodayNotice",
    clickAction = "REFRESH",
  ) =>
    widgetTaskHandler({
      widgetInfo: {
        widgetName,
        widgetId: id,
        width,
        height: 370,
      },
      widgetAction: action,
      clickAction,
      renderWidget: (element) =>
        renders.push({ id, component: element.type, ...element.props }),
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

test("카드형 위젯도 최근 공문을 조회하고 새로고침하되 알 수 없는 위젯은 무시한다", async () => {
  const h = harness();
  await h.run(9, "WIDGET_UPDATE", 350, "Unknown");
  assert.equal(h.pending.length, 0);
  const update = h.run(1, "WIDGET_ADDED", 350, "NoticeCard");
  const result = {
    status: "ready",
    date: "2026-10-09",
    notice: { title: "최근 공문" },
  };
  h.pending[0](result);
  await update;
  const card = h.renders.at(-1).component;
  assert.equal(h.renders.at(-1).state, result);
  const refresh = h.run(1, "WIDGET_CLICK", 350, "NoticeCard");
  assert.equal(h.renders.at(-1).state.status, "loading");
  h.pending[1]({ status: "empty" });
  await refresh;
  assert.equal(h.renders.at(-1).component, card);
  const letter = h.run(2);
  h.pending[2]({ status: "empty" });
  await letter;
  assert.notEqual(h.renders.at(-1).component, card);
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

test("상세 열기는 조회나 새로고침을 발생시키지 않는다", async () => {
  const h = harness();
  await h.run(1, "WIDGET_CLICK", 350, "NoticeCard", "OPEN_URI");
  await h.run(1, "WIDGET_CLICK", 350, "NoticeCard", "OPEN_APP");
  assert.equal(h.pending.length, 0);
  assert.equal(h.renders.length, 0);
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
