const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
require("./loadTypeScript.cjs");

// 화면 대신 Hook에 앱 이벤트와 제어 가능한 애니메이션 완료 콜백을 주입한다.
async function createOrbit(t) {
  const slots = [];
  const effects = [];
  const jobs = [];
  let cursor = 0;
  let appListener;
  const changed = (previous, next) =>
    !previous || next.some((value, index) => value !== previous[index]);
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [
        slots[index],
        (value) => {
          slots[index] = value;
        },
      ];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!slots[index] || changed(slots[index].deps, deps))
        slots[index] = { fn, deps };
      return slots[index].fn;
    },
    useEffect(fn, deps) {
      const index = cursor++;
      if (!slots[index] || changed(slots[index].deps, deps)) {
        effects.push(() => {
          slots[index]?.dispose?.();
          slots[index] = { deps, dispose: fn() };
        });
      }
    },
  };
  const callback = (animation) =>
    animation?.sequence?.at(-1)?.callback ?? animation?.callback;
  const shared = (initial) => {
    const ref = react.useRef(null);
    if (ref.current === null) {
      ref.current = {
        value: initial,
        animation: null,
        get() {
          return this.value;
        },
        set(value) {
          if (this.animation) {
            const cancelled = this.animation;
            this.animation = null;
            callback(cancelled)?.(false);
          }
          if (typeof value === "number") this.value = value;
          else this.animation = value;
        },
      };
    }
    return ref.current;
  };
  const mocks = {
    react,
    "react-native": {
      AppState: {
        currentState: "active",
        addEventListener: (_name, fn) => {
          appListener = fn;
          return { remove() {} };
        },
      },
      AccessibilityInfo: {
        isReduceMotionEnabled: async () => false,
        addEventListener: () => ({ remove() {} }),
      },
    },
    "expo-router": { useFocusEffect: (fn) => react.useEffect(fn, [fn]) },
    "react-native-worklets": {
      scheduleOnRN: (fn, ...args) => jobs.push(() => fn(...args)),
    },
    "react-native-reanimated": {
      useSharedValue: shared,
      useDerivedValue() {},
      Easing: { bezier: () => "ease" },
      ReduceMotion: { Never: "never" },
      withTiming: (value, config, done) => ({ value, config, callback: done }),
      withSequence: (...sequence) => ({ sequence }),
      cancelAnimation: (value) => value.set(value.get()),
    },
    "../domain/letterOrbit": require("../src/features/notices/domain/letterOrbit.ts"),
    "../domain/letterOrbitInput": require("../src/features/notices/domain/letterOrbitInput.ts"),
    "../components/letterInteraction": require("../src/features/notices/components/letterInteraction.ts"),
  };
  const compiled = ts.transpileModule(
    fs.readFileSync(
      path.join(__dirname, "../src/features/notices/hooks/useLetterOrbit.ts"),
      "utf8",
    ),
    {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    },
  ).outputText;
  const loaded = { exports: {} };
  new Function("exports", "require", compiled)(loaded.exports, (name) => {
    assert.ok(name in mocks, `Unexpected import: ${name}`);
    return mocks[name];
  });
  let data = [
    { id: "a", title: "old" },
    { id: "b", title: "second" },
    { id: "c", title: "third" },
  ];
  const render = (next = data) => {
    data = next;
    cursor = 0;
    const value = loaded.exports.useLetterOrbit(data, 375);
    effects.splice(0).forEach((fn) => fn());
    return value;
  };
  const flush = () => {
    while (jobs.length) jobs.shift()();
  };
  render();
  await new Promise((resolve) => setImmediate(resolve));
  render();
  render();
  t.after(() => slots.forEach((slot) => slot?.dispose?.()));
  return {
    render,
    flush,
    background: () => appListener("background"),
    complete(value, done = true) {
      const animation = value.animation;
      assert.ok(animation, "애니메이션이 시작되어야 한다");
      value.animation = null;
      if (done)
        value.value = animation.sequence?.at(-1)?.value ?? animation.value;
      callback(animation)?.(done);
      flush();
    },
    callback: (value) => callback(value.animation),
  };
}

test("중복 이동을 무시하고 강제 취소 후 다시 입력할 수 있다", async (t) => {
  const host = await createOrbit(t);
  const orbit = host.render();
  orbit.move(1);
  orbit.move(-1);
  assert.equal(host.render().transition.to, 1);
  host.complete(orbit.clock, false);
  assert.equal(host.render().busy, false);
  assert.equal(host.render().center, 0);
  host.render().move(-1);
  host.complete(orbit.clock);
  assert.equal(host.render().center, -1);
});

test("내용만 갱신되면 전환을 유지하고 완료 후 최신 내용으로 돌아온다", async (t) => {
  const host = await createOrbit(t);
  const orbit = host.render();
  orbit.move(1);
  const fresh = host
    .render()
    .displayNotices.map((notice) => ({ ...notice, title: "fresh" }));
  host.render(fresh);
  assert.equal(host.render().busy, true);
  assert.equal(host.render().displayNotices[0].title, "old");
  host.complete(orbit.clock);
  assert.equal(host.render().displayNotices[0].title, "fresh");
});

test("대상 삭제로 취소한 뒤 오래된 완료 콜백이 새 이동을 끝내지 않는다", async (t) => {
  const host = await createOrbit(t);
  const orbit = host.render();
  orbit.move(1);
  const stale = host.callback(orbit.clock);
  host.render([{ id: "a" }, { id: "c" }]);
  host.flush();
  assert.equal(host.render().busy, false);
  host.render().move(1);
  stale(true);
  host.flush();
  assert.equal(host.render().busy, true);
  host.complete(orbit.clock);
  assert.equal(host.render().center, 1);
});

test("작은 드래그와 취소는 현재 공문으로 복귀한다", async (t) => {
  const host = await createOrbit(t);
  const orbit = host.render();
  orbit.beginDrag();
  orbit.endDrag(-40, 0);
  host.complete(orbit.clock);
  assert.equal(host.render().center, 0);
  host.render().beginDrag();
  host.render().endDrag(-220, -800, true);
  host.complete(orbit.position);
  host.complete(orbit.clock);
  assert.equal(host.render().center, 0);
  assert.equal(host.render().busy, false);
});

test("스냅 완료 후 편지를 열고 백그라운드 취소 후 잠금을 해제한다", async (t) => {
  const host = await createOrbit(t);
  const orbit = host.render();
  orbit.beginDrag();
  orbit.endDrag(-180, 0);
  host.complete(orbit.position);
  assert.equal(host.render().transition.to, 1);
  host.complete(orbit.clock);
  assert.equal(host.render().center, 1);
  host.render().move(1);
  host.background();
  host.flush();
  assert.equal(host.render().busy, false);
  assert.equal(host.render().center, 1);
});
