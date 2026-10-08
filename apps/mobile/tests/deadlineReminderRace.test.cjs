/* global __dirname */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { loadTs } = require("./support/loadTs.cjs");
const tick = () => new Promise((resolve) => setImmediate(resolve));

function screen(initial) {
  const hooks = [];
  let cursor = 0,
    effect,
    resume;
  const pending = [];
  let reads = 0;
  const jsx = (type, props) => ({ type, props });
  const component = loadTs(
    path.join(__dirname, "../src/features/notifications/DeadlineReminder.tsx"),
    {
      react: {
        useState: (value) => {
          const i = cursor++;
          if (!(i in hooks)) hooks[i] = value;
          return [
            hooks[i],
            (value) => {
              hooks[i] = value;
            },
          ];
        },
        useRef: (value) => {
          const i = cursor++;
          if (!(i in hooks)) hooks[i] = { current: value };
          return hooks[i];
        },
        useEffect: (fn) => {
          effect = fn;
        },
      },
      "react/jsx-runtime": { jsx, jsxs: jsx },
      "react-native": {
        Platform: { OS: "android" },
        StyleSheet: { create: (styles) => styles },
        View: "View",
        TextInput: "TextInput",
        Pressable: "Pressable",
        AppState: {
          addEventListener: (_, fn) => {
            resume = fn;
            return { remove() {} };
          },
        },
      },
      "@/shared/ui/AppText": { AppText: "AppText" },
      "@/shared/theme/tokens": { COLORS: {} },
      "./reminderService": {
        getReminder: () =>
          ++reads === 1
            ? Promise.resolve(initial)
            : new Promise((resolve, reject) =>
                pending.push({ resolve, reject }),
              ),
        cancelReminder: async () => {},
        scheduleReminder: async () => {},
      },
    },
  );
  const render = () => {
    cursor = 0;
    return component.DeadlineReminder({
      notice: { id: "43", summaryStatus: "none", title: "공지" },
    });
  };
  const nodes = (node) => {
    if (!node) return [];
    if (Array.isArray(node)) return node.flatMap(nodes);
    return node.props ? [node, ...nodes(node.props.children)] : [];
  };
  const find = (predicate) =>
    nodes(render()).find((node) => predicate(node.props));
  render();
  effect();
  return {
    pending,
    resume: () => resume("active"),
    press: (label) => {
      const node = find((p) => p.label === label);
      assert.ok(node);
      node.props.onPress();
    },
    has: (label) => !!find((p) => p.label === label),
    text: () => JSON.stringify(render()),
    input: (prefix, value) =>
      find((p) => p.accessibilityLabel?.startsWith(prefix)).props.onChangeText(
        value,
      ),
  };
}

test("late refresh cannot resurrect a cancelled reservation or replace success with a stale error", async () => {
  for (const fail of [false, true]) {
    const s = screen(new Date(Date.now() + 3600000));
    await tick();
    s.resume();
    s.press("예약 취소");
    await tick();
    if (fail) s.pending[0].reject(new Error("old read failed"));
    else s.pending[0].resolve(new Date(Date.now() + 3600000));
    await tick();
    assert.equal(s.has("예약 취소"), false);
    assert.match(s.text(), /예약을 취소했어요/);
    assert.doesNotMatch(s.text(), /예약 정보를 확인하지 못했어요/);
  }
});

test("late refresh cannot erase a newly changed reservation", async () => {
  const s = screen(new Date(Date.now() + 3600000));
  await tick();
  s.resume();
  s.input("알림 날짜", "2099-12-01");
  s.input("알림 시간", "10:30");
  s.press("선택한 시간으로 변경");
  await tick();
  s.pending[0].resolve(null);
  await tick();
  assert.equal(s.has("예약 취소"), true);
  assert.match(s.text(), /2099-12-01 10:30/);
});

test("only the newest refresh is reflected and delayed reservations retain cancel", async () => {
  const s = screen(new Date(1));
  await tick();
  assert.equal(s.has("예약 취소"), true);
  assert.match(s.text(), /예정 시각이 지났지만/);
  s.resume();
  s.resume();
  s.pending[1].resolve(null);
  await tick();
  s.pending[0].resolve(new Date(1));
  await tick();
  assert.equal(s.has("예약 취소"), false);
});
