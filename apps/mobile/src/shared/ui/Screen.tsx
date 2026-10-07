import {
  Animated,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useMemo, useState, type ReactNode } from "react";
import { COLORS, EASY, SPACE } from "@/shared/theme/tokens";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { usePreferenceStorageStatus } from "@/shared/accessibility/safePreferenceStorage";
import { AppText } from "./AppText";

/** 공통 safe area·최대 너비·스크롤을 제공하고, 떠 있는 탭바가 있으면 하단 여백을 확보한다. */
export function Screen({
  header,
  children,
  floating = false,
  contentStyle,
  backgroundColor,
  bottomSafe = false,
  headerBehavior = "fixed",
  overlay,
}: {
  header?: ReactNode;
  children: ReactNode;
  floating?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
  backgroundColor?: string;
  bottomSafe?: boolean;
  headerBehavior?: "fixed" | "scroll" | "reveal";
  overlay?: ReactNode;
}) {
  const easy = useDisplayPreferences((state) => state.mode === "easy");
  const storageAvailable = usePreferenceStorageStatus(
    (state) => state.available,
  );
  const [scrollY] = useState(() => new Animated.Value(0));
  const [headerHeight, setHeaderHeight] = useState(0);
  const hiddenHeight = useMemo(
    () => Animated.diffClamp(scrollY, 0, headerHeight),
    [scrollY, headerHeight],
  );
  const content = (
    <>
      {!storageAvailable && (
        <AppText
          size={easy ? EASY.body : 14.77}
          accessibilityLiveRegion="polite"
        >
          이 기기에 설정을 저장하지 못했습니다. 설정은 이번 실행 동안만
          유지됩니다.
        </AppText>
      )}
      {children}
    </>
  );
  const bodyStyle = [
    styles.content,
    { paddingBottom: floating ? 110 : 36 },
    contentStyle,
  ];
  return (
    <SafeAreaView
      edges={
        bottomSafe
          ? ["top", "left", "right", "bottom"]
          : ["top", "left", "right"]
      }
      style={[
        styles.safe,
        {
          backgroundColor:
            backgroundColor ?? (easy ? COLORS.soft : COLORS.surface),
        },
      ]}
    >
      <View
        style={[styles.width, headerBehavior === "reveal" && styles.clipHeader]}
      >
        {headerBehavior === "fixed" && header}
        <Animated.ScrollView
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
          contentContainerStyle={
            headerBehavior === "fixed" ? bodyStyle : undefined
          }
          bounces={headerBehavior !== "reveal"}
          scrollEventThrottle={16}
          onScroll={
            headerBehavior === "reveal"
              ? Animated.event(
                  [{ nativeEvent: { contentOffset: { y: scrollY } } }],
                  { useNativeDriver: false },
                )
              : undefined
          }
        >
          {headerBehavior === "scroll" && header}
          {headerBehavior === "reveal" && (
            <View style={{ height: headerHeight }} />
          )}
          {headerBehavior === "fixed" ? (
            content
          ) : (
            <View style={bodyStyle}>{content}</View>
          )}
        </Animated.ScrollView>
        {headerBehavior === "reveal" && (
          // 검색 입력을 재생성하지 않고 Android·Web에서 같은 방향 기반 동작을 제공한다.
          <Animated.View
            onLayout={(event) =>
              setHeaderHeight(event.nativeEvent.layout.height)
            }
            style={[
              styles.revealHeader,
              {
                backgroundColor: backgroundColor ?? COLORS.surface,
                transform: [
                  { translateY: Animated.multiply(hiddenHeight, -1) },
                ],
              },
            ]}
          >
            {header}
          </Animated.View>
        )}
        {overlay}
      </View>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.surface },
  width: { flex: 1, width: "100%", maxWidth: 600, alignSelf: "center" },
  clipHeader: { overflow: "hidden" },
  revealHeader: { position: "absolute", top: 0, left: 0, right: 0, zIndex: 1 },
  content: { paddingHorizontal: SPACE.xl, paddingTop: 7.23, gap: SPACE.lg },
});
