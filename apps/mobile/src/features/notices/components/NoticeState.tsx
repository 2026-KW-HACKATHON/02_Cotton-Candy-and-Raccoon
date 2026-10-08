import { ActivityIndicator, StyleSheet, View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { noticeErrorPresentation } from "../domain/noticeError";
import { NoticeActionButton } from "./NoticeActionButton";
export function NoticeState({
  loading,
  error,
  errorDetail,
  message,
  retry,
  retrying = false,
  comfortable = false,
}: {
  loading?: boolean;
  error?: boolean;
  errorDetail?: unknown;
  message?: string;
  retry?: () => void;
  retrying?: boolean;
  comfortable?: boolean;
}) {
  const failure = noticeErrorPresentation(errorDetail);
  return (
    <View style={styles.box} accessibilityLiveRegion="polite">
      {loading && <ActivityIndicator color={COLORS.primary} />}
      <AppText
        variant="bold"
        size={comfortable ? 20 : 16}
        style={{ textAlign: "center" }}
      >
        {loading
          ? "공문을 불러오는 중이에요."
          : error
            ? failure.title
            : message}
      </AppText>
      {error && (
        <AppText
          secondary
          size={comfortable ? 20 : 14}
          style={{ textAlign: "center" }}
        >
          {failure.description}
        </AppText>
      )}
      {error && failure.retryable && retry && (
        <NoticeActionButton
          label={retrying ? "불러오는 중…" : "다시 불러오기"}
          comfortable={comfortable}
          filled
          disabled={retrying}
          onPress={retry}
        />
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  box: {
    padding: 24,
    gap: 12,
    backgroundColor: COLORS.soft,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
});
