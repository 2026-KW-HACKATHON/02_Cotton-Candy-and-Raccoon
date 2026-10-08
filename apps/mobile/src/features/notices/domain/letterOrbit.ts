// 기존 양옆 봉투의 가로 간격과 높이 차이로 원을 맞춘다. 그림 기울기는 기존 12도를 유지한다.
export const ORBIT_ANGLE = 2 * Math.atan(50.882 / 210);
export const ORBIT_RADIUS = 210 / Math.sin(ORBIT_ANGLE);

export function wrapNoticeIndex(position: number, count: number) {
  return count > 0 ? ((position % count) + count) % count : -1;
}

export function getOrbitPose(slot: number, position: number, scale: number) {
  "worklet";
  const distance = slot - position;
  const angle = distance * ORBIT_ANGLE;
  return {
    x: ORBIT_RADIUS * Math.sin(angle) * scale,
    y: ORBIT_RADIUS * (1 - Math.cos(angle)) * scale,
    rotation: distance * (Math.PI / 15),
    opacity: 1 - Math.min(1, Math.abs(distance)) * 0.5,
  };
}

export function getOrbitSlots(center: number) {
  return Array.from({ length: 7 }, (_, index) => center + index - 3);
}
