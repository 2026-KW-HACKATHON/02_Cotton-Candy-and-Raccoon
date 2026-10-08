import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { preferenceStorage } from "./preferenceStorage";

export type DisplayMode = "easy" | "standard";
export const FONT_SCALES = [0.9, 1, 1.15] as const;
type SettingsState = {
  fontScale: number;
  setFontScale: (value: number) => void;
  mode: DisplayMode;
  setMode: (mode: DisplayMode) => void;
  onboardingComplete: boolean;
  completeOnboarding: (mode: DisplayMode) => void;
};
// 화면 방식과 글자 크기만 영구 저장한다. 서버 데이터와 화면 내부의 임시 선택은 저장하지 않는다.
export const useDisplayPreferences = create<SettingsState>()(
  persist(
    (set) => ({
      fontScale: 1,
      setFontScale: (fontScale) => {
        if (FONT_SCALES.some((value) => value === fontScale))
          set({ fontScale });
      },
      mode: "standard",
      setMode: (mode) => set({ mode }),
      onboardingComplete: false,
      completeOnboarding: (mode) => set({ mode, onboardingComplete: true }),
    }),
    {
      name: "wolgyenotice-display-preferences",
      storage: createJSONStorage(() => preferenceStorage),
      skipHydration: true,
      // 로컬 저장소의 값도 외부 입력이므로 타입 선언만 믿지 않고 허용된 값만 복원한다.
      merge: (persisted, current) => {
        if (typeof persisted !== "object" || persisted === null) return current;
        const saved = persisted as Record<string, unknown>;
        const mode =
          saved.mode === "easy" || saved.mode === "standard"
            ? saved.mode
            : current.mode;
        return {
          ...current,
          mode,
          fontScale: FONT_SCALES.some((value) => value === saved.fontScale)
            ? (saved.fontScale as number)
            : current.fontScale,
          onboardingComplete: saved.onboardingComplete === true,
        };
      },
      partialize: ({ mode, fontScale, onboardingComplete }) => ({
        mode,
        fontScale,
        onboardingComplete,
      }),
    },
  ),
);
