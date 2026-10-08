import { create } from "zustand";
import type { NoticeSource } from "../types/notice";

export const NOTICE_SCOPES = [
  { label: "전체", source: undefined },
  { label: "서울시", source: "seoul" },
  { label: "노원구", source: "nowon" },
  { label: "월계1동", source: "dong" },
] as const;

// API 조회와 메뉴 표시가 같은 지역 값을 사용한다.
export const useNoticeScopeStore = create<{
  source: NoticeSource | undefined;
  setSource: (source: NoticeSource | undefined) => void;
}>((set) => ({
  source: undefined,
  setSource: (source) => set({ source }),
}));
