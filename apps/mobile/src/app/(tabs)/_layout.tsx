import { Tabs } from "expo-router";
import { FloatingTabBar } from "@/shared/ui/FloatingTabBar";
import { EasyNavigation } from "@/shared/ui/EasyNavigation";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";

export default function TabLayout() {
  const mode = useDisplayPreferences((state) => state.mode);
  return (
    <Tabs
      screenOptions={{ headerShown: false }}
      tabBar={(props) =>
        mode === "easy" ? <EasyNavigation /> : <FloatingTabBar {...props} />
      }
    >
      <Tabs.Screen name="index" options={{ title: "홈" }} />
      <Tabs.Screen name="notices" options={{ title: "전체 공문" }} />
      <Tabs.Screen name="saved" options={{ title: "보관함" }} />
    </Tabs>
  );
}
