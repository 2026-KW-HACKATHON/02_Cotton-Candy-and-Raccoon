import { create } from "zustand";

export const NOTICE_SCOPES = ["서울시", "노원구", "월계1동"] as const;
export type NoticeScope = (typeof NOTICE_SCOPES)[number];

// 지역 선택만 화면 간 공유한다. 현재 예시 데이터는 모두 월계1동 공문이다.
export const useNoticeScopeStore = create<{
  scope: NoticeScope;
  setScope: (scope: NoticeScope) => void;
}>((set) => ({
  scope: "월계1동",
  setScope: (scope) => set({ scope }),
}));
