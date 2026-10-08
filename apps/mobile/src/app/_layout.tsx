import { useKeywordNotifications } from "@/features/keyword-notifications/useKeywordNotifications";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { QueryClientProvider } from "@tanstack/react-query";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { queryClient } from "@/shared/lib/queryClient";
import { COLORS } from "@/shared/theme/tokens";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";

import { useAppStartup } from "@/features/settings/hooks/useAppStartup";
import { AnimatedSplashScreen } from "@/features/settings/screens/AnimatedSplashScreen";

export default function RootLayout() {
  const startup = useAppStartup();
  const onboardingComplete = useDisplayPreferences(
    (state) => state.onboardingComplete,
  );
  useKeywordNotifications(startup.ready && onboardingComplete);
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <StatusBar style="dark" />
        {!startup.ready ? (
          startup.assetsReady ? (
            <AnimatedSplashScreen
              onVisible={startup.onSplashVisible}
              fontsReady={startup.fontsReady}
            />
          ) : null
        ) : (
          <Stack
            screenOptions={{
              headerShown: false,
              contentStyle: { backgroundColor: COLORS.surface },
            }}
          >
            <Stack.Protected guard={onboardingComplete}>
              <Stack.Screen name="(tabs)" />
              <Stack.Screen name="settings" />
              <Stack.Screen name="keyword-notifications" />
              <Stack.Screen name="notice/[id]" />
            </Stack.Protected>
            <Stack.Screen name="onboarding" />
          </Stack>
        )}
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
