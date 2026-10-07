import { useEffect, useState } from "react";
import { Stack } from "expo-router";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { QueryClientProvider } from "@tanstack/react-query";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { Jua_400Regular } from "@expo-google-fonts/jua/400Regular";
import { NotoSansKR_400Regular } from "@expo-google-fonts/noto-sans-kr/400Regular";
import { NotoSansKR_500Medium } from "@expo-google-fonts/noto-sans-kr/500Medium";
import { NotoSansKR_700Bold } from "@expo-google-fonts/noto-sans-kr/700Bold";
import { queryClient } from "@/shared/lib/queryClient";
import { COLORS } from "@/shared/theme/tokens";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";

// 글꼴 로딩 전 화면 노출을 늦춘다. 이미 숨겨진 스플래시 등 중복 호출 실패는 무시한다.
void SplashScreen.preventAutoHideAsync().catch(() => undefined);

export default function RootLayout() {
  const [hydrated, setHydrated] = useState(false);
  const onboardingComplete = useDisplayPreferences(
    (state) => state.onboardingComplete,
  );
  useEffect(() => {
    // 저장소를 사용할 수 없는 환경에서도 초기 설정으로 앱에 진입할 수 있게 한다.
    void Promise.resolve(useDisplayPreferences.persist.rehydrate()).finally(
      () => setHydrated(true),
    );
  }, []);
  const [fontsLoaded, fontError] = useFonts({
    Jua: Jua_400Regular,
    NotoSansKR: NotoSansKR_400Regular,
    NotoSansKRMedium: NotoSansKR_500Medium,
    NotoSansKRBold: NotoSansKR_700Bold,
  });
  // 글꼴 로딩 실패 시에도 스플래시를 해제해 앱 진입을 막지 않는다.
  useEffect(() => {
    if (hydrated && (fontsLoaded || fontError)) void SplashScreen.hideAsync();
  }, [fontsLoaded, fontError, hydrated]);
  if (!hydrated || (!fontsLoaded && !fontError)) return null;
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <StatusBar style="dark" />
        <Stack
          screenOptions={{
            headerShown: false,
            contentStyle: { backgroundColor: COLORS.surface },
          }}
        >
          <Stack.Protected guard={onboardingComplete}>
            <Stack.Screen name="(tabs)" />
            <Stack.Screen name="settings" />
            <Stack.Screen name="notice/[id]" />
          </Stack.Protected>
          <Stack.Screen name="onboarding" />
        </Stack>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
