import { Pressable, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { router, usePathname } from "expo-router";
import { AppText } from "./AppText";
import { COLORS, EASY } from "@/shared/theme/tokens";

const TABS = [
  { path: "/", label: "홈" },
  { path: "/notices", label: "전체\n공문" },
  { path: "/saved", label: "저장한\n공문" },
  { path: "/settings", label: "설정" },
] as const;
export function EasyNavigation() {
  const pathname = usePathname();
  return (
    <SafeAreaView edges={["bottom", "left", "right"]} style={styles.safe}>
      <View
        style={styles.bar}
        accessibilityRole="tablist"
        accessibilityLabel="주요 화면"
      >
        {TABS.map((tab) => {
          const selected = pathname === tab.path;
          return (
            <Pressable
              key={tab.path}
              accessibilityRole="tab"
              accessibilityLabel={tab.label.replace("\n", " ")}
              accessibilityState={{ selected }}
              aria-selected={selected}
              onPress={() => router.navigate(tab.path)}
              style={[styles.tab, selected && styles.selected]}
            >
              <AppText
                size={EASY.body}
                variant={selected ? "bold" : "body"}
                style={{
                  textAlign: "center",
                  color: selected ? COLORS.primary : COLORS.secondary,
                }}
              >
                {tab.label}
              </AppText>
              {selected && <View style={styles.indicator} />}
            </Pressable>
          );
        })}
      </View>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  safe: {
    backgroundColor: COLORS.surface,
    borderTopWidth: 1,
    borderColor: COLORS.border,
  },
  bar: {
    flexDirection: "row",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 8,
    maxWidth: 600,
    width: "100%",
    alignSelf: "center",
  },
  tab: {
    flex: 1,
    minHeight: 72,
    padding: 4,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 12,
  },
  selected: { backgroundColor: COLORS.soft },
  indicator: { width: 24, height: 3, backgroundColor: COLORS.primary },
});
