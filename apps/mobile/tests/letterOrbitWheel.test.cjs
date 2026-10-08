const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

function loadWheel(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const effects = [];
  const starts = [];
  const ends = [];
  const input = {
    value: 0,
    set(value) {
      this.value = value;
    },
  };
  let listener;
  let removed = false;
  const react = {
    useRef: (value) => ({ current: value }),
    useCallback: (callback) => callback,
    useEffect: (effect) => effects.push(effect),
  };
  const loaded = { exports: {} };
  const compiled = ts.transpileModule(
    fs.readFileSync(
      path.join(
        __dirname,
        "../src/features/notices/hooks/useLetterOrbitWheel.ts",
      ),
      "utf8",
    ),
    {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    },
  ).outputText;
  new Function("exports", "require", compiled)(loaded.exports, (name) => {
    assert.equal(name, "react");
    return react;
  });
  const ref = loaded.exports.useLetterOrbitWheel({
    width: 375,
    enabled: true,
    busy: false,
    dragInput: input,
    beginDrag() {
      starts.push(true);
      return 11;
    },
    endDrag(...args) {
      ends.push(args);
    },
  });
  ref.current = {
    addEventListener(name, callback, options) {
      assert.equal(name, "wheel");
      assert.equal(options.passive, false);
      listener = callback;
    },
    removeEventListener() {
      removed = true;
      listener = undefined;
    },
  };
  const cleanup = effects.map((effect) => effect());
  t.after(() => cleanup.forEach((fn) => fn?.()));
  return {
    starts,
    ends,
    input,
    wheel(deltaX, deltaY, deltaMode = 0) {
      let prevented = false;
      listener({
        deltaX,
        deltaY,
        deltaMode,
        preventDefault() {
          prevented = true;
        },
      });
      return prevented;
    },
    dispose: () => cleanup.forEach((fn) => fn?.()),
    removed: () => removed,
  };
}

test("가로 휠 입력을 누적하고 마지막 입력 이후 한 번만 스냅한다", (t) => {
  const wheel = loadWheel(t);
  assert.equal(wheel.wheel(80, 0), true);
  t.mock.timers.tick(100);
  wheel.wheel(100, 0);
  t.mock.timers.tick(139);
  assert.equal(wheel.ends.length, 0);
  t.mock.timers.tick(1);
  assert.equal(wheel.starts.length, 1);
  assert.equal(wheel.input.value, -180);
  assert.deepEqual(wheel.ends, [[-180, 0, false, 11]]);
});

test("세로 스크롤은 가로 이동을 시작하지 않고 제거 후에는 예약된 스냅을 실행하지 않는다", (t) => {
  const wheel = loadWheel(t);
  assert.equal(wheel.wheel(2, 30), false);
  assert.equal(wheel.starts.length, 0);
  wheel.wheel(5, 0, 1);
  assert.equal(wheel.input.value, -80);
  wheel.dispose();
  t.mock.timers.tick(200);
  assert.equal(wheel.ends.length, 0);
  assert.equal(wheel.removed(), true);
});
