import { StyleSheet } from "react-native";
import { Settings } from "lucide-react-native";
import { router } from "expo-router";
import { COLORS, FLOATING_SURFACE, SPACE } from "@/shared/theme/tokens";
import { IconButton } from "./IconButton";

export function FloatingSettingsButton() {
  return (
    <IconButton
      accessibilityLabel="설정 열기"
      onPress={() => router.push("/settings")}
      style={styles.button}
    >
      <Settings size={22} color={COLORS.secondary} strokeWidth={1.5} />
    </IconButton>
  );
}
const styles = StyleSheet.create({
  button: {
    position: "absolute",
    top: 9.231,
    right: SPACE.xl,
    width: 44.308,
    height: 44.308,
    ...FLOATING_SURFACE,
    zIndex: 2,
  },
});
