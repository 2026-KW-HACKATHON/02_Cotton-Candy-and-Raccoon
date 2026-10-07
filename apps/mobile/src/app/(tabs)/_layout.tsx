import { Tabs } from "expo-router";
import { FloatingTabBar } from "@/shared/ui/FloatingTabBar";

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{ headerShown: false }}
      tabBar={(props) => <FloatingTabBar {...props} />}
    >
      <Tabs.Screen name="index" options={{ title: "홈" }} />
      <Tabs.Screen name="notices" options={{ title: "전체 공문" }} />
      <Tabs.Screen name="saved" options={{ title: "보관함" }} />
    </Tabs>
  );
}
