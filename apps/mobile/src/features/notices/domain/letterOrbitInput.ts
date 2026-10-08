export const DRAG_DEAD_ZONE = 24;
export const DRAG_CLOSE_DISTANCE = 40;
export const SNAP_THRESHOLD = 0.45;

export function getDragPose(translation: number, spacing: number) {
  "worklet";
  const distance = Math.max(0, Math.abs(translation) - DRAG_DEAD_ZONE);
  return {
    stage: Math.min(5, (distance / DRAG_CLOSE_DISTANCE) * 5),
    offset:
      -Math.sign(translation) *
      Math.min(1, Math.max(0, distance - DRAG_CLOSE_DISTANCE) / spacing),
  };
}

export function getSnapDirection(offset: number, velocity: number): -1 | 0 | 1 {
  "worklet";
  // 거의 움직이지 않은 상태에서는 속도 잡음으로 공문을 바꾸지 않는다.
  if (Math.abs(offset) < 0.12) return 0;
  const projected = Math.max(-1, Math.min(1, offset + velocity * 0.12));
  return Math.abs(projected) >= SNAP_THRESHOLD ? (projected > 0 ? 1 : -1) : 0;
}

export function haveSameNoticeOrder(
  previous: readonly { id: string }[],
  next: readonly { id: string }[],
) {
  return (
    previous.length === next.length &&
    previous.every((notice, index) => notice.id === next[index].id)
  );
}

export function getEdgeTouchWidth(width: number, paperWidth: number) {
  // 중앙 그림 바깥 12dp는 비워 두고, 좁은 화면에서는 클릭 폭을 줄인다.
  return Math.max(0, Math.min(56, (width - paperWidth) / 2 - 12));
}
