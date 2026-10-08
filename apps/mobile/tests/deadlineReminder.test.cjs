/* global __dirname */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { loadTs } = require("./support/loadTs.cjs");
const root = path.join(__dirname, "../src/features/notifications");
const domain = loadTs(path.join(root, "reminderDate.ts"));

test("local date validation rejects rollover, invalid times and malformed inputs", () => {
  for (const [date, time] of [
    ["2027-02-29", "09:00"],
    ["2028-02-30", "09:00"],
    ["2028-13-01", "09:00"],
    ["2028-01-01", "24:00"],
    ["2028-01-01", "09:60"],
    ["2028-1-1", "09:00"],
    ["", ""],
  ]) {
    assert.throws(() => domain.parseReminderDate(date, time));
  }
  const date = domain.parseReminderDate("2028-02-29", "09:30");
  assert.equal(date.getHours(), 9);
  assert.equal(date.getMinutes(), 30);
  assert.equal(domain.dateInput(date), "2028-02-29");
  assert.equal(domain.presetDate("2028-03-01", 1), "2028-02-29");
  assert.equal(domain.presetDate("2028-01-01", 1), "2027-12-31");
});

test("notification routing only accepts our payload and a positive numeric ID", () => {
  assert.equal(
    domain.reminderNoticeId({ kind: domain.REMINDER_KIND, noticeId: "43" }),
    "43",
  );
  for (const payload of [
    undefined,
    {},
    { kind: "other", noticeId: "43" },
    { kind: domain.REMINDER_KIND, noticeId: "../settings" },
    { kind: domain.REMINDER_KIND, noticeId: "0" },
  ]) {
    assert.equal(domain.reminderNoticeId(payload), null);
  }
});

function fixture(options = {}) {
  const stored = new Map();
  const calls = [];
  const api = {
    AndroidImportance: { HIGH: 4, NONE: 0 },
    SchedulableTriggerInputTypes: { DATE: "date" },
    setNotificationChannelAsync: async () => calls.push("channel"),
    getNotificationChannelAsync: async () => ({
      importance: options.channelOff ? 0 : 4,
    }),
    getPermissionsAsync: async () => ({
      granted: !!options.granted,
      canAskAgain: !options.permanent,
    }),
    requestPermissionsAsync: async () => {
      calls.push("permission");
      return { granted: !options.denied };
    },
    scheduleNotificationAsync: async (request) => {
      calls.push("schedule");
      if (options.fail) throw new Error("native scheduling failed");
      stored.set(request.identifier, request);
      return request.identifier;
    },
    getAllScheduledNotificationsAsync: async () => [...stored.values()],
    cancelScheduledNotificationAsync: async (id) => stored.delete(id),
  };
  const loadService = () =>
    loadTs(path.join(root, "reminderService.ts"), {
      "react-native": { Platform: { OS: options.os || "android" } },
      "expo-notifications": api,
    });
  return { service: loadService(), loadService, stored, calls };
}
const future = () => new Date(Date.now() + 3600_000);

test("replacement uses one stable native ID, survives module reload and supports cancel", async () => {
  const f = fixture();
  await f.service.scheduleReminder("43", "공지", future());
  const later = new Date(Date.now() + 7200_000);
  await f.service.scheduleReminder("43", "공지 수정", later);
  assert.equal(f.stored.size, 1);
  assert.deepEqual(f.calls.slice(0, 3), ["channel", "permission", "schedule"]);
  assert.equal(
    (await f.loadService().getReminder("43")).getTime(),
    later.getTime(),
  );
  await f.service.scheduleReminder("44", "다른 공지", future());
  await f.service.cancelReminder("43");
  assert.equal(await f.service.getReminder("43"), null);
  assert.equal(f.stored.size, 1);
});

test("denied permission and disabled channel never schedule", async () => {
  for (const options of [
    { denied: true },
    { permanent: true },
    { channelOff: true },
  ]) {
    const f = fixture(options);
    await assert.rejects(f.service.scheduleReminder("43", "공지", future()));
    assert.equal(f.stored.size, 0);
    assert.ok(!f.calls.includes("schedule"));
    if (options.permanent) assert.ok(!f.calls.includes("permission"));
  }
});

test("past dates, invalid IDs and web are rejected", async () => {
  const f = fixture();
  for (const date of [new Date(0), new Date(NaN)])
    await assert.rejects(f.service.scheduleReminder("43", "공지", date));
  await assert.rejects(f.service.scheduleReminder("bad", "공지", future()));
  await assert.rejects(
    fixture({ os: "web" }).service.scheduleReminder("43", "공지", future()),
  );
  assert.equal(f.stored.size, 0);
});

test("native errors propagate and delayed OS requests remain visible and cancellable", async () => {
  const f = fixture({ fail: true });
  await assert.rejects(
    f.service.scheduleReminder("43", "공지", future()),
    /native scheduling/,
  );
  f.stored.set(domain.reminderIdentifier("43"), {
    identifier: domain.reminderIdentifier("43"),
    content: { data: { scheduledAt: 1 } },
  });
  assert.equal((await f.service.getReminder("43")).getTime(), 1);
  await f.service.cancelReminder("43");
  assert.equal(await f.service.getReminder("43"), null);
});

test("cold start and live tap navigate once, and late results after unmount are ignored", async () => {
  const response = {
    actionIdentifier: "default",
    notification: {
      date: 123,
      request: {
        identifier: "notice-deadline-v1:43",
        content: { data: { kind: domain.REMINDER_KIND, noticeId: "43" } },
      },
    },
  };
  let effect, listener, resolveLast;
  const navigated = [];
  let removed = false;
  const component = loadTs(path.join(root, "NotificationNavigation.tsx"), {
    react: {
      useEffect: (fn) => {
        effect = fn;
      },
    },
    "react-native": { Platform: { OS: "android" } },
    "expo-router": {
      useRouter: () => ({ push: (route) => navigated.push(route) }),
      useRootNavigationState: () => ({ key: "ready" }),
    },
    "expo-notifications": {
      DEFAULT_ACTION_IDENTIFIER: "default",
      setNotificationHandler: () => {},
      addNotificationResponseReceivedListener: (fn) => {
        listener = fn;
        return {
          remove: () => {
            removed = true;
          },
        };
      },
      getLastNotificationResponseAsync: () =>
        new Promise((resolve) => {
          resolveLast = resolve;
        }),
      clearLastNotificationResponseAsync: async () => {},
    },
  });
  component.NotificationNavigation();
  const dispose = effect();
  await new Promise((resolve) => setImmediate(resolve));
  listener(response);
  resolveLast(response);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(navigated, [
    { pathname: "/notice/[id]", params: { id: "43" } },
  ]);
  dispose();
  assert.equal(removed, true);
  listener({
    ...response,
    notification: { ...response.notification, date: 456 },
  });
  assert.equal(navigated.length, 1);
});
