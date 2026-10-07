import { Pressable, StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { type ComponentProps } from "react";
import { Tabs } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { COLORS } from "@/shared/theme/tokens";

const TABS = [
  {
    name: "notices",
    label: "전체 공문",
    image: require("@/assets/figma/home-imgIconNavigationStackedEnvelopes.svg"),
  },
  {
    name: "index",
    label: "홈",
    image: require("@/assets/figma/home-imgIconNavigationHome.svg"),
  },
  {
    name: "saved",
    label: "보관함",
    image: require("@/assets/figma/home-imgIconNavigationArchiveBox.svg"),
  },
];
// 별도 내비게이션 패키지 타입 대신 현재 Expo Router의 tabBar 타입을 사용한다.
type TabBarProps = Parameters<
  NonNullable<ComponentProps<typeof Tabs>["tabBar"]>
>[0];
export function FloatingTabBar({ state, navigation }: TabBarProps) {
  const insets = useSafeAreaInsets();
  return (
    <View
      pointerEvents="box-none"
      style={[styles.position, { bottom: insets.bottom + 14 }]}
    >
      <View
        style={styles.bar}
        accessibilityRole="tablist"
        accessibilityLabel="주요 화면"
      >
        {TABS.map((tab) => {
          const route = state.routes.find((item) => item.name === tab.name);
          if (!route) return null;
          const selected = state.routes[state.index].key === route.key;
          return (
            <Pressable
              key={tab.name}
              accessibilityRole="tab"
              accessibilityLabel={tab.label}
              accessibilityState={{ selected }}
              aria-selected={selected}
              style={styles.tab}
              onPress={() => {
                // 기본 탭 이벤트를 전달해 리스너가 화면 이동을 취소할 수 있도록 한다.
                const event = navigation.emit({
                  type: "tabPress",
                  target: route.key,
                  canPreventDefault: true,
                });
                if (!selected && !event.defaultPrevented)
                  navigation.navigate(route.name);
              }}
            >
              <View style={[styles.selection, selected && styles.selected]}>
                <Image
                  source={tab.image}
                  style={styles.icon}
                  contentFit="contain"
                />
              </View>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}
const styles = StyleSheet.create({
  position: { position: "absolute", left: 0, right: 0, alignItems: "center" },
  bar: {
    width: 262.154,
    height: 66.462,
    padding: 7.385,
    borderRadius: 999,
    flexDirection: "row",
    backgroundColor: "rgba(254,253,251,0.96)",
    shadowColor: COLORS.text,
    shadowOpacity: 0.12,
    shadowOffset: { width: 0, height: 6 },
    shadowRadius: 6,
    elevation: 6,
  },
  tab: { flex: 1, alignItems: "center", justifyContent: "center" },
  selection: {
    width: 44.308,
    height: 44.308,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
  },
  selected: { backgroundColor: COLORS.soft },
  icon: { width: 29.5385, height: 29.5385 },
});
