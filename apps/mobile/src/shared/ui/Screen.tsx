import {
  ScrollView,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { type ReactNode } from "react";
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
}: {
  header?: ReactNode;
  children: ReactNode;
  floating?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
  backgroundColor?: string;
  bottomSafe?: boolean;
}) {
  const easy = useDisplayPreferences((state) => state.mode === "easy");
  const storageAvailable = usePreferenceStorageStatus((state) => state.available);
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
      <View style={styles.width}>
        {header}
        <ScrollView
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
          contentContainerStyle={[
            styles.content,
            { paddingBottom: floating ? 110 : 36 },
            contentStyle,
          ]}
        >
          {!storageAvailable && (
            <AppText
              size={easy ? EASY.body : 14.77}
              accessibilityLiveRegion="polite"
            >
              이 기기에 설정을 저장하지 못했습니다. 설정은 이번 실행 동안만 유지됩니다.
            </AppText>
          )}
          {children}
        </ScrollView>
      </View>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.surface },
  width: { flex: 1, width: "100%", maxWidth: 600, alignSelf: "center" },
  content: { paddingHorizontal: SPACE.xl, paddingTop: 7.23, gap: SPACE.lg },
});
