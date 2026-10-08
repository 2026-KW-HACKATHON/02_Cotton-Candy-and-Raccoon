export type LetterState = "reading" | "lifted" | "inserting" | "closed";

// Flow 1의 전환 시간은 그대로 두고, 들어 올림·완전 삽입·읽기 정렬은 추가 구간이다.
export const LETTER_STEPS = [
  { state: "lifted", duration: 200 },
  { state: "inserting", duration: 280 },
  { state: "inserting", duration: 180 },
  { state: "closed", duration: 180 },
  { state: "closed", duration: 140 },
  { state: "closed", duration: 420 },
  { state: "closed", duration: 160 },
  { state: "closed", duration: 180 },
  { state: "inserting", duration: 180 },
  { state: "lifted", duration: 300 },
  { state: "reading", duration: 200 },
] as const;

// Figma의 -128/340 비율은 기존 홈의 상단 공간을 넘으므로 정지 배치를 유지하는 범위로 제한한다.
export const PAPER_LIFT = -32;
export const PAPER_INSERT = (80 * 290) / 340;
// 그림의 가장 높은 부위까지 pocket 하단(504px) 아래로 내려 완전히 숨긴다.
export const PAPER_CONCEALED = 520;

export type LetterRole = "reading" | "outgoing" | "incoming" | "closed";

function between(
  value: number,
  start: number,
  end: number,
  from: number,
  to: number,
) {
  "worklet";
  const progress = Math.max(0, Math.min(1, (value - start) / (end - start)));
  return from + (to - from) * progress;
}

export function getLetterMotion(stage: number, role: LetterRole) {
  "worklet";
  if (role === "closed")
    return { paperY: PAPER_CONCEALED, open: 0, flap: 1, paperAbove: false };
  if (role === "reading")
    return { paperY: 0, open: 1, flap: 0, paperAbove: true };
  if (role === "outgoing") {
    const paperY =
      stage < 1
        ? between(stage, 0, 1, 0, PAPER_LIFT)
        : stage < 2
          ? between(stage, 1, 2, PAPER_LIFT, PAPER_INSERT)
          : between(stage, 2, 3, PAPER_INSERT, PAPER_CONCEALED);
    return {
      paperY,
      open: between(stage, 3, 5, 1, 0),
      flap:
        stage < 4
          ? between(stage, 3, 4, -1, 0.018)
          : between(stage, 4, 5, 0.018, 1),
      paperAbove: stage <= 1,
    };
  }
  const paperY =
    stage < 9
      ? between(stage, 8, 9, PAPER_CONCEALED, PAPER_INSERT)
      : stage < 10
        ? between(stage, 9, 10, PAPER_INSERT, PAPER_LIFT)
        : between(stage, 10, 11, PAPER_LIFT, 0);
  return {
    paperY,
    open: between(stage, 6, 8, 0, 1),
    flap:
      stage < 7
        ? between(stage, 6, 7, 1, 0.018)
        : between(stage, 7, 8, 0.018, -1),
    paperAbove: stage >= 10,
  };
}
