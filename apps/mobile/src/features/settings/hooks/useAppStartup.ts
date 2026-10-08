import { useCallback, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";
import { Image } from "expo-image";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import { Jua_400Regular } from "@expo-google-fonts/jua/400Regular";
import { NotoSansKR_400Regular } from "@expo-google-fonts/noto-sans-kr/400Regular";
import { NotoSansKR_500Medium } from "@expo-google-fonts/noto-sans-kr/500Medium";
import { NotoSansKR_700Bold } from "@expo-google-fonts/noto-sans-kr/700Bold";
import { SPLASH_IMAGE_SOURCES } from "@/shared/ui/character/AnimatedCharacter";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";

// 첫 3초에 등장·깜빡임·고개 동작이 완료된다. 준비가 더 오래 걸리면 6초 타임라인을 계속 반복한다.
const INTRO_DURATION_MS = 3000;
void SplashScreen.preventAutoHideAsync().catch(() => undefined);

export function useAppStartup() {
  const [hydrated, setHydrated] = useState(false);
  const [assetsReady, setAssetsReady] = useState(false);
  const [introComplete, setIntroComplete] = useState(false);
  const visible = useRef(false);
  const foreground = useRef(
    AppState.currentState !== "background" &&
      AppState.currentState !== "inactive",
  );
  const introFinished = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startIntroTimer = useCallback(() => {
    if (!foreground.current || introFinished.current) return;
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      if (!foreground.current) return;
      introFinished.current = true;
      setIntroComplete(true);
    }, INTRO_DURATION_MS);
  }, []);
  const [fontsLoaded, fontError] = useFonts({
    Jua: Jua_400Regular,
    NotoSansKR: NotoSansKR_400Regular,
    NotoSansKRMedium: NotoSansKR_500Medium,
    NotoSansKRBold: NotoSansKR_700Bold,
  });
  useEffect(() => {
    let mounted = true;
    const subscription = AppState.addEventListener("change", (state) => {
      foreground.current = state === "active";
      if (!foreground.current) {
        if (timer.current !== null) clearTimeout(timer.current);
        timer.current = null;
      } else if (visible.current) {
        // 모션 시계도 복귀 시 처음부터 재생하므로 최소 표시 시간도 함께 다시 센다.
        startIntroTimer();
      }
    });
    if (visible.current) startIntroTimer();
    void Promise.resolve(useDisplayPreferences.persist.rehydrate())
      .catch(() => undefined)
      .finally(() => {
        if (mounted) setHydrated(true);
      });
    // 에셋 로딩 실패도 앱 진입을 막지 않으며, 정상 경로에서는 부위별 그림의 뒤늦은 노출을 방지한다.
    void Promise.all(
      SPLASH_IMAGE_SOURCES.map((source) => Image.loadAsync(source)),
    )
      .catch(() => undefined)
      .finally(() => {
        if (mounted) setAssetsReady(true);
      });
    return () => {
      mounted = false;
      subscription.remove();
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = null;
    };
  }, [startIntroTimer]);
  const onSplashVisible = useCallback(() => {
    if (visible.current) return;
    visible.current = true;
    void SplashScreen.hideAsync().catch(() => undefined);
    startIntroTimer();
  }, [startIntroTimer]);
  return {
    assetsReady,
    fontsReady: fontsLoaded,
    onSplashVisible,
    ready:
      assetsReady &&
      hydrated &&
      Boolean(fontsLoaded || fontError) &&
      introComplete,
  };
}
