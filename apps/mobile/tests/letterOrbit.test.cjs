const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

function load(relativePath) {
  const source = fs.readFileSync(path.join(__dirname, relativePath), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const loaded = { exports: {} };
  new Function("module", "exports", compiled)(loaded, loaded.exports);
  return loaded.exports;
}

const { wrapNoticeIndex, getOrbitPose, getOrbitSlots, ORBIT_RADIUS } = load(
  "../src/features/notices/domain/letterOrbit.ts",
);
const { getLetterMotion, PAPER_LIFT, PAPER_INSERT, PAPER_CONCEALED } = load(
  "../src/features/notices/components/letterInteraction.ts",
);
const near = (actual, expected) =>
  assert.ok(Math.abs(actual - expected) < 0.000001, `${actual} != ${expected}`);

test("처음과 마지막을 양방향으로 연결하고 빈 목록에서 modulo를 계산하지 않는다", () => {
  assert.equal(wrapNoticeIndex(0, 0), -1);
  assert.equal(wrapNoticeIndex(-1, 3), 2);
  assert.equal(wrapNoticeIndex(3, 3), 0);
  assert.equal(wrapNoticeIndex(-7, 3), 2);
  assert.equal(wrapNoticeIndex(700, 1), 0);
  assert.equal(wrapNoticeIndex(-1, 2), wrapNoticeIndex(1, 2));
});

test("한 바퀴 연결에도 다음 봉투가 같은 방향으로 중앙에 진입한다", () => {
  const start = getOrbitPose(3, 2, 1);
  const middle = getOrbitPose(3, 2.5, 1);
  const end = getOrbitPose(3, 3, 1);
  assert.ok(start.x > middle.x && middle.x > end.x);
  assert.ok(start.y > middle.y && middle.y > end.y);
  near(end.x, 0);
  near(end.y, 0);
  assert.equal(wrapNoticeIndex(3, 3), 0);
  near(getOrbitPose(-1, -1, 1).x, 0);
  assert.equal(wrapNoticeIndex(-1, 3), 2);
});

test("중간 프레임도 같은 원 위에 놓이고 정지 위치는 기존 양옆 배치와 맞는다", () => {
  for (const progress of [0, 0.2, 0.5, 0.8, 1]) {
    const pose = getOrbitPose(1, progress, 0.8);
    const radius = ORBIT_RADIUS * 0.8;
    near(Math.hypot(pose.x, radius - pose.y), radius);
  }
  near(getOrbitPose(1, 0, 1).x, 210);
  near(getOrbitPose(1, 0, 1).y, 50.882);
  near(getOrbitPose(-1, 0, 1).rotation, -Math.PI / 15);
});

test("슬롯 재배치가 보이는 봉투의 위치를 바꾸지 않는다", () => {
  const before = getOrbitSlots(2);
  const after = getOrbitSlots(3);
  for (const slot of [2, 3, 4]) {
    assert.ok(before.includes(slot) && after.includes(slot));
    assert.equal(wrapNoticeIndex(slot, 3), [2, 0, 1][slot - 2]);
  }
  assert.deepEqual(
    before.filter((slot) => after.includes(slot)),
    [0, 1, 2, 3, 4, 5],
  );
  assert.equal(new Set(getOrbitSlots(-3)).size, 7);
});

test("회전 구간에는 두 편지가 모두 완전히 들어가 있고 봉투가 닫혀 있다", () => {
  for (const stage of [5, 5.25, 5.5, 5.75, 6]) {
    for (const role of ["outgoing", "incoming"]) {
      const pose = getLetterMotion(stage, role);
      assert.equal(pose.paperY, PAPER_CONCEALED);
      assert.equal(pose.open, 0);
      assert.equal(pose.flap, 1);
    }
  }
});

test("들어 올림·넣기·닫힘을 거쳐 새 편지가 원래 읽기 위치로 돌아온다", () => {
  assert.equal(getLetterMotion(1, "outgoing").paperY, PAPER_LIFT);
  assert.equal(getLetterMotion(2, "outgoing").paperY, PAPER_INSERT);
  assert.equal(getLetterMotion(3, "outgoing").paperY, PAPER_CONCEALED);
  assert.equal(getLetterMotion(4, "outgoing").flap, 0.018);
  assert.equal(getLetterMotion(9, "incoming").paperY, PAPER_INSERT);
  assert.equal(getLetterMotion(10, "incoming").paperY, PAPER_LIFT);
  assert.deepEqual(getLetterMotion(11, "incoming"), {
    ...getLetterMotion(0, "reading"),
    flap: -1,
  });
  for (const stage of [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]) {
    for (const role of ["incoming", "outgoing"]) {
      assert.ok(
        Math.abs(
          getLetterMotion(stage + 0.000001, role).paperY -
            getLetterMotion(stage, role).paperY,
        ) < 0.001,
      );
    }
  }
});
