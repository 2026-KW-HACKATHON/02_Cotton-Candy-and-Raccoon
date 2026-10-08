const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

// 네이티브 환경 대신 앱 상태 이벤트와 가상 시간을 주입해 시작 Hook의 화면 전환 조건을 검증한다.
async function loadStartup(t, initialState = "active") {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const slots = [];
  const effects = [];
  let cursor = 0;
  let listener;
  let removed = false;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => { slots[index] = value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useCallback(callback) { return callback; },
    useEffect(effect) {
      const index = cursor++;
      if (!(index in slots)) {
        slots[index] = true;
        effects.push(effect);
      }
    },
  };
  const appState = {
    currentState: initialState,
    addEventListener(_event, callback) {
      listener = callback;
      return { remove() { removed = true; listener = undefined; } };
    },
  };
  const source = fs.readFileSync(
    path.join(__dirname, "../src/features/settings/hooks/useAppStartup.ts"),
    "utf8",
  );
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  const mocks = {
    react,
    "react-native": { AppState: appState },
    "expo-image": { Image: { loadAsync: async () => ({}) } },
    "expo-font": { useFonts: () => [true, null] },
    "expo-splash-screen": {
      preventAutoHideAsync: async () => {},
      hideAsync: async () => {},
    },
    "@/shared/ui/character/AnimatedCharacter": { SPLASH_IMAGE_SOURCES: [1] },
    "@/shared/accessibility/displayPreferences": {
      useDisplayPreferences: { persist: { rehydrate: async () => {} } },
    },
  };
  const loaded = { exports: {} };
  new Function("exports", "require", compiled)(loaded.exports, (name) => {
    if (name.startsWith("@expo-google-fonts/")) return {};
    assert.ok(name in mocks, `Unexpected import: ${name}`);
    return mocks[name];
  });
  const render = () => {
    cursor = 0;
    return loaded.exports.useAppStartup();
  };
  render();
  const cleanup = effects.map((effect) => effect());
  await new Promise((resolve) => setImmediate(resolve));
  t.after(() => cleanup.forEach((dispose) => dispose?.()));
  return {
    render,
    changeState(state) { appState.currentState = state; listener?.(state); },
    unmount() { cleanup.forEach((dispose) => dispose?.()); },
    isRemoved: () => removed,
  };
}

test("스플래시가 표시된 뒤 3초가 지나야 앱에 진입한다", async (t) => {
  const startup = await loadStartup(t);
  t.mock.timers.tick(5000);
  assert.equal(startup.render().ready, false);
  startup.render().onSplashVisible();
  t.mock.timers.tick(2999);
  assert.equal(startup.render().ready, false);
  t.mock.timers.tick(1);
  assert.equal(startup.render().ready, true);
});

test("백그라운드 시간은 제외하고 복귀 후 3초를 다시 표시한다", async (t) => {
  const startup = await loadStartup(t);
  startup.render().onSplashVisible();
  t.mock.timers.tick(1000);
  startup.changeState("background");
  t.mock.timers.tick(5000);
  assert.equal(startup.render().ready, false);
  startup.changeState("active");
  t.mock.timers.tick(2999);
  assert.equal(startup.render().ready, false);
  t.mock.timers.tick(1);
  assert.equal(startup.render().ready, true);
  startup.changeState("background");
  startup.changeState("active");
  assert.equal(startup.render().ready, true);
});

test("백그라운드에서 표시되면 활성화될 때부터 시간을 센다", async (t) => {
  const startup = await loadStartup(t, "background");
  startup.render().onSplashVisible();
  t.mock.timers.tick(5000);
  assert.equal(startup.render().ready, false);
  startup.changeState("active");
  t.mock.timers.tick(3000);
  assert.equal(startup.render().ready, true);
});

test("언마운트 시 시작 타이머와 앱 상태 구독을 정리한다", async (t) => {
  const startup = await loadStartup(t);
  startup.render().onSplashVisible();
  startup.unmount();
  t.mock.timers.tick(3000);
  assert.equal(startup.render().ready, false);
  assert.equal(startup.isRemoved(), true);
});
