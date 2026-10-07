import { StyleSheet, View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS, RADIUS } from "@/shared/theme/tokens";

export function CategoryBadge({ children }: { children: string }) {
  return (
    <View style={styles.badge}>
      <AppText size={12.92} style={{ color: COLORS.primary }}>
        {children}
      </AppText>
    </View>
  );
}
const styles = StyleSheet.create({
  badge: {
    backgroundColor: COLORS.soft,
    borderRadius: RADIUS.badge,
    alignSelf: "flex-start",
    minHeight: 25.846,
    paddingHorizontal: 18,
    justifyContent: "center",
  },
});
