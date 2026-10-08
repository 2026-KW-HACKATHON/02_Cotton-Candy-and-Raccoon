import { Pressable, StyleSheet } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
export function NoticeActionButton({
  label,
  onPress,
  comfortable = false,
  filled = false,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  comfortable?: boolean;
  filled?: boolean;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        {
          minHeight: comfortable ? 56 : 48,
          backgroundColor: filled ? COLORS.primary : COLORS.surface,
          opacity: disabled ? 0.45 : pressed ? 0.8 : 1,
        },
      ]}
    >
      <AppText
        variant="bold"
        size={comfortable ? 20 : 16}
        style={{
          color: filled ? COLORS.surface : COLORS.primary,
          textAlign: "center",
        }}
      >
        {label}
      </AppText>
    </Pressable>
  );
}
const styles = StyleSheet.create({
  button: {
    borderWidth: 1,
    borderColor: COLORS.primary,
    borderRadius: 12,
    padding: 12,
    alignItems: "center",
    justifyContent: "center",
  },
});
