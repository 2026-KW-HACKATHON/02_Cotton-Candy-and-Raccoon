const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

function loadModule(relativePath, requireModule = require) {
  const source = fs.readFileSync(path.join(__dirname, relativePath), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const loaded = { exports: {} };
  new Function("module", "exports", "require", compiled)(
    loaded,
    loaded.exports,
    requireModule,
  );
  return loaded.exports;
}

function loadStorage() {
  return loadModule("../src/shared/accessibility/safePreferenceStorage.ts");
}

test("웹 저장소 접근 자체가 거절돼도 이번 실행의 설정을 읽고 제거할 수 있다", async () => {
  const { createSafePreferenceStorage, usePreferenceStorageStatus } = loadStorage();
  const storage = createSafePreferenceStorage(() => {
    throw new Error("SecurityError");
  });
  assert.equal(await storage.getItem("preferences"), null);
  await storage.setItem("preferences", "current");
  assert.equal(await storage.getItem("preferences"), "current");
  await storage.removeItem("preferences");
  assert.equal(await storage.getItem("preferences"), null);
  assert.equal(usePreferenceStorageStatus.getState().available, false);
});

test("쓰기 실패 후 오래된 저장 값으로 현재 설정을 되돌리지 않는다", async () => {
  const { createSafePreferenceStorage } = loadStorage();
  const storage = createSafePreferenceStorage(() => ({
    getItem: () => "old",
    setItem: () => { throw new Error("QuotaExceededError"); },
    removeItem: () => {},
  }));
  assert.equal(await storage.getItem("preferences"), "old");
  await storage.setItem("preferences", "current");
  assert.equal(await storage.getItem("preferences"), "current");
});

test("네이티브 저장소의 비동기 실패도 처리하고 온보딩·설정 변경을 유지한다", async () => {
  const { createSafePreferenceStorage, usePreferenceStorageStatus } = loadStorage();
  const storage = createSafePreferenceStorage(() => ({
    getItem: async () => null,
    setItem: async () => { throw new Error("SQLite write failed"); },
    removeItem: async () => {},
  }));
  const { useDisplayPreferences: store } = loadModule(
    "../src/shared/accessibility/displayPreferences.ts",
    (name) => name === "./preferenceStorage"
      ? { preferenceStorage: storage }
      : require(name),
  );
  await store.persist.rehydrate();
  assert.doesNotThrow(() => store.getState().completeOnboarding("easy"));
  // 비동기 저장 실패가 처리된 이후에도 다음 설정 동작과 재수화가 정상이어야 한다.
  await new Promise(setImmediate);
  store.getState().setFontScale(1.15);
  await store.persist.rehydrate();
  assert.equal(store.getState().mode, "easy");
  assert.equal(store.getState().fontScale, 1.15);
  assert.equal(store.getState().onboardingComplete, true);
  assert.equal(usePreferenceStorageStatus.getState().available, false);
});

test("비동기 읽기·삭제 실패를 호출자에게 전파하지 않는다", async () => {
  const { createSafePreferenceStorage } = loadStorage();
  const reading = createSafePreferenceStorage(() => ({
    getItem: async () => { throw new Error("Read failed"); },
    setItem: async () => {},
    removeItem: async () => {},
  }));
  assert.equal(await reading.getItem("preferences"), null);
  const removing = createSafePreferenceStorage(() => ({
    getItem: async () => "old",
    setItem: async () => {},
    removeItem: async () => { throw new Error("Remove failed"); },
  }));
  await removing.removeItem("preferences");
  assert.equal(await removing.getItem("preferences"), null);
});

test("정상 저장소에서는 새 실행에도 설정이 복원된다", async () => {
  const { createSafePreferenceStorage, usePreferenceStorageStatus } = loadStorage();
  const values = new Map();
  const persistent = {
    getItem: (name) => values.get(name) ?? null,
    setItem: (name, value) => { values.set(name, value); },
    removeItem: (name) => { values.delete(name); },
  };
  await createSafePreferenceStorage(() => persistent).setItem("preferences", "saved");
  assert.equal(
    await createSafePreferenceStorage(() => persistent).getItem("preferences"),
    "saved",
  );
  assert.equal(usePreferenceStorageStatus.getState().available, true);
});
