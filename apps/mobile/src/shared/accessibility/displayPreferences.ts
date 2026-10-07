import { create } from "zustand";

type SettingsState = {
  fontScale: number;
  setFontScale: (value: number) => void;
};
// 설정 화면과 공통 텍스트가 함께 사용하는 앱 내부 배율이다. 현재는 실행 중에만 유지한다.
export const useDisplayPreferences = create<SettingsState>((set) => ({
  fontScale: 1,
  setFontScale: (fontScale) => set({ fontScale }),
}));
