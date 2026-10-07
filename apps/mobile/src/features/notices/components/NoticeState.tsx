import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS, RADIUS } from "@/shared/theme/tokens";

export function NoticeState({
  loading,
  error,
  message,
  retry,
}: {
  loading?: boolean;
  error?: boolean;
  message?: string;
  retry?: () => void;
}) {
  return (
    <View style={styles.box} accessibilityLiveRegion="polite">
      {loading && <ActivityIndicator color={COLORS.primary} />}
      <AppText secondary style={{ textAlign: "center" }}>
        {loading
          ? "공문을 불러오고 있어요."
          : error
            ? "공문을 불러오지 못했어요."
            : message}
      </AppText>
      {error && (
        <Pressable
          accessibilityRole="button"
          onPress={retry}
          style={styles.retry}
        >
          <AppText style={{ color: COLORS.primary }}>다시 시도</AppText>
        </Pressable>
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  box: {
    padding: 24,
    gap: 12,
    backgroundColor: COLORS.soft,
    borderRadius: RADIUS.card,
  },
  retry: { minHeight: 44, alignItems: "center", justifyContent: "center" },
});
