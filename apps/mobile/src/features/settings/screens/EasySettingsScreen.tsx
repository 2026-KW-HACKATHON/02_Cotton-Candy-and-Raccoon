import { StyleSheet, View } from "react-native";
import { router } from "expo-router";
import { Screen } from "@/shared/ui/Screen";
import { goBack } from "@/shared/ui/Header";
import { AppText } from "@/shared/ui/AppText";
import { EasyButton } from "@/shared/ui/EasyButton";
import { EasyNavigation } from "@/shared/ui/EasyNavigation";
import {
  FONT_SCALES,
  useDisplayPreferences,
} from "@/shared/accessibility/displayPreferences";
import { COLORS, EASY } from "@/shared/theme/tokens";

export function EasySettingsScreen() {
  const { mode, fontScale, setMode, setFontScale } = useDisplayPreferences();
  const index = FONT_SCALES.findIndex((value) => value === fontScale);
  return (
    <View style={{ flex: 1 }}>
      <Screen
        contentStyle={{
          paddingHorizontal: EASY.inset,
          paddingTop: EASY.inset,
          gap: EASY.gap,
        }}
      >
        <EasyButton label="이전 화면으로" onPress={goBack} />
        <AppText size={EASY.title} variant="display">
          설정
        </AppText>
        <View style={styles.panel}>
          <AppText size={EASY.heading} variant="display">
            화면 방식
          </AppText>
          <EasyButton
            label={`편한 화면${mode === "easy" ? " · 선택됨" : ""}`}
            filled={mode === "easy"}
            selected={mode === "easy"}
            onPress={() => setMode("easy")}
          />
          <EasyButton
            label={`일반 화면${mode === "standard" ? " · 선택됨" : ""}`}
            filled={mode === "standard"}
            selected={mode === "standard"}
            onPress={() => {
              setMode("standard");
              router.replace("/");
            }}
          />
        </View>
        <View style={styles.panel}>
          <AppText size={EASY.heading} variant="display">
            글자 크기
          </AppText>
          <AppText size={EASY.body} accessibilityLiveRegion="polite">
            현재 글자 크기 · {["작게", "기본", "크게"][index]}
          </AppText>
          <EasyButton
            label="글자 작게"
            disabled={index <= 0}
            onPress={() => setFontScale(FONT_SCALES[Math.max(0, index - 1)])}
          />
          <EasyButton
            label="글자 크게"
            filled
            disabled={index >= FONT_SCALES.length - 1}
            onPress={() =>
              setFontScale(
                FONT_SCALES[Math.min(FONT_SCALES.length - 1, index + 1)],
              )
            }
          />
          <AppText size={EASY.body}>이 크기로 공문을 읽습니다.</AppText>
        </View>
        <EasyButton
          label="관심 키워드 알림"
          onPress={() => router.push("/keyword-notifications")}
        />
        <EasyButton
          label="사용 설명 다시 보기"
          onPress={() => router.push("/onboarding")}
        />
      </Screen>
      <EasyNavigation />
    </View>
  );
}
const styles = StyleSheet.create({
  panel: {
    padding: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: EASY.cardRadius,
    backgroundColor: COLORS.surface,
    gap: 12,
  },
});
