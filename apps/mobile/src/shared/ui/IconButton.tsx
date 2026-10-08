import { Pressable, StyleSheet, type PressableProps } from "react-native";
import { COLORS } from "@/shared/theme/tokens";

export function IconButton({ children, style, ...props }: PressableProps) {
  return (
    <Pressable
      {...props}
      accessibilityRole={props.accessibilityRole ?? "button"}
      style={(state) => [
        styles.button,
        state.pressed && { opacity: 0.6 },
        typeof style === "function" ? style(state) : style,
      ]}
    >
      {children}
    </Pressable>
  );
}
const styles = StyleSheet.create({
  button: {
    minWidth: 44.308,
    minHeight: 44.308,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 999,
    backgroundColor: COLORS.surface,
  },
});
