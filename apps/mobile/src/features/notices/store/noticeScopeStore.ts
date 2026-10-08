import { create } from "zustand";

export const NOTICE_SCOPES = ["서울시", "노원구", "월계1동"] as const;
export type NoticeScope = (typeof NOTICE_SCOPES)[number];

// 지역 선택을 공유하고 해당 출처를 목록 조회 키와 서버 필터에 반영한다.
export const useNoticeScopeStore = create<{
  scope: NoticeScope;
  setScope: (scope: NoticeScope) => void;
}>((set) => ({
  scope: "월계1동",
  setScope: (scope) => set({ scope }),
}));
