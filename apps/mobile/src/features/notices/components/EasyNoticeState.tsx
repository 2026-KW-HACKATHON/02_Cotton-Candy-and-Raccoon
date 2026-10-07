import { StyleSheet, View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { EasyButton } from "@/shared/ui/EasyButton";
import { COLORS, EASY } from "@/shared/theme/tokens";

export function EasyNoticeState({
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
    <View style={{ gap: EASY.gap }}>
      <View
        style={[styles.box, error && { backgroundColor: COLORS.soft }]}
        accessibilityLiveRegion="polite"
      >
        <AppText size={EASY.body} variant="bold">
          {loading
            ? "공문을 불러오는 중입니다"
            : error
              ? "공문을 불러오지 못했습니다"
              : message}
        </AppText>
      </View>
      {error && retry && (
        <EasyButton label="다시 불러오기" filled onPress={retry} />
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  box: {
    backgroundColor: EASY.muted,
    borderRadius: EASY.cardRadius,
    borderColor: COLORS.border,
    borderWidth: 1,
    padding: 20,
  },
});
