const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

function loadStore(values = new Map()) {
  const source = fs.readFileSync(
    path.join(__dirname, "../src/shared/accessibility/displayPreferences.ts"),
    "utf8",
  );
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const loaded = { exports: {} };
  const storage = {
    getItem: (name) => values.get(name) ?? null,
    setItem: (name, value) => values.set(name, value),
    removeItem: (name) => values.delete(name),
  };
  new Function("module", "exports", "require", compiled)(
    loaded,
    loaded.exports,
    (name) =>
      name === "./preferenceStorage"
        ? { preferenceStorage: storage }
        : require(name),
  );
  return loaded.exports.useDisplayPreferences;
}

test("화면 방식·글자 크기·온보딩 완료 상태를 다음 store에 복원한다", async () => {
  const values = new Map();
  const first = loadStore(values);
  first.getState().completeOnboarding("easy");
  first.getState().setFontScale(1.15);
  const second = loadStore(values);
  assert.equal(second.getState().onboardingComplete, false);
  await second.persist.rehydrate();
  assert.equal(second.getState().mode, "easy");
  assert.equal(second.getState().fontScale, 1.15);
  assert.equal(second.getState().onboardingComplete, true);
});

test("잘못 저장된 배율과 화면 방식은 기본값으로 복원한다", async () => {
  const values = new Map([
    [
      "wolgyenotice-display-preferences",
      JSON.stringify({
        state: { mode: "unknown", fontScale: -20, onboardingComplete: "true" },
        version: 0,
      }),
    ],
  ]);
  const store = loadStore(values);
  await store.persist.rehydrate();
  assert.equal(store.getState().mode, "standard");
  assert.equal(store.getState().fontScale, 1);
  assert.equal(store.getState().onboardingComplete, false);
  assert.equal(typeof store.getState().setMode, "function");
});

test("화면 방식 변경만으로 온보딩이 완료되지 않고 허용되지 않은 배율은 적용하지 않는다", () => {
  const store = loadStore();
  store.getState().setMode("easy");
  store.getState().setFontScale(2);
  assert.equal(store.getState().onboardingComplete, false);
  assert.equal(store.getState().fontScale, 1);
});
