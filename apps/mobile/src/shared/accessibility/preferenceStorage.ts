import { type StateStorage } from "zustand/middleware";

// 웹 정적 렌더링 중에는 window가 없으므로 저장소 접근을 클라이언트 수화까지 미룬다.
export const preferenceStorage: StateStorage = {
  getItem: (name) =>
    typeof window === "undefined" ? null : window.localStorage.getItem(name),
  setItem: (name, value) => {
    if (typeof window !== "undefined") window.localStorage.setItem(name, value);
  },
  removeItem: (name) => {
    if (typeof window !== "undefined") window.localStorage.removeItem(name);
  },
};
