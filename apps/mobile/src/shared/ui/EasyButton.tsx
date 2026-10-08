import {
  Pressable,
  StyleSheet,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import { AppText } from "./AppText";
import { COLORS, EASY } from "@/shared/theme/tokens";

export function EasyButton({
  label,
  subtitle,
  onPress,
  filled = false,
  disabled = false,
  selected,
  style,
}: {
  label: string;
  subtitle?: string;
  onPress: () => void;
  filled?: boolean;
  disabled?: boolean;
  selected?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const color = disabled
    ? COLORS.secondary
    : filled
      ? COLORS.surface
      : COLORS.primary;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={[label, subtitle].filter(Boolean).join(", ")}
      accessibilityState={{ disabled, selected }}
      aria-pressed={selected}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        filled && styles.filled,
        disabled && styles.disabled,
        pressed && { opacity: 0.8 },
        style,
      ]}
    >
      <AppText
        size={EASY.body}
        variant="bold"
        style={{ color, textAlign: "center" }}
      >
        {label}
      </AppText>
      {subtitle && (
        <AppText
          size={16}
          variant="bold"
          style={{ color, textAlign: "center" }}
        >
          {subtitle}
        </AppText>
      )}
    </Pressable>
  );
}
const styles = StyleSheet.create({
  button: {
    minHeight: EASY.buttonHeight,
    borderWidth: 1,
    borderColor: "#73899B",
    borderRadius: EASY.buttonRadius,
    backgroundColor: COLORS.surface,
    paddingHorizontal: 12,
    paddingVertical: 10,
    justifyContent: "center",
    alignItems: "center",
    gap: 2,
  },
  filled: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  disabled: { backgroundColor: EASY.disabled, borderColor: EASY.disabled },
});
