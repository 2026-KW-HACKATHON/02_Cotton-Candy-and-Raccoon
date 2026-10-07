import { StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { ChevronLeft, Settings } from "lucide-react-native";
import { router } from "expo-router";
import { AppText } from "./AppText";
import { IconButton } from "./IconButton";
import { CARD_SHADOW, COLORS, SPACE } from "@/shared/theme/tokens";

export function goBack() {
  if (router.canGoBack()) router.back();
  else router.replace("/");
}
export function Header({
  title,
  back = false,
  right,
}: {
  title: string;
  back?: boolean;
  right?: React.ReactNode;
}) {
  return (
    <View style={styles.header}>
      {back && (
        <IconButton accessibilityLabel="뒤로 가기" onPress={goBack}>
          <ChevronLeft color={COLORS.secondary} size={20} strokeWidth={1.5} />
        </IconButton>
      )}
      <View style={[styles.title, back && styles.center]}>
        <AppText variant="display" size={back ? 22.15 : 25.85}>
          {title}
        </AppText>
        {!back && (
          <Image
            source={require("@/assets/figma/home-imgEllipse3.svg")}
            style={styles.dot}
          />
        )}
      </View>
      {right ??
        (back ? (
          <View style={styles.balance} />
        ) : (
          <IconButton
            accessibilityLabel="설정 열기"
            style={CARD_SHADOW}
            onPress={() => router.push("/settings")}
          >
            <Settings size={22} color={COLORS.secondary} strokeWidth={1.5} />
          </IconButton>
        ))}
    </View>
  );
}
const styles = StyleSheet.create({
  header: {
    minHeight: 62.77,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: SPACE.xl,
    gap: 8,
  },
  title: { flex: 1, flexDirection: "row", alignItems: "baseline", gap: 4 },
  center: { justifyContent: "center" },
  balance: { width: 44.308 },
  dot: { width: 6.46154, height: 6.46154 },
});
