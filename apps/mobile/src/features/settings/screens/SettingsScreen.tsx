import { useState } from "react";
import { Modal, Pressable, StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { router } from "expo-router";
import { ChevronRight } from "lucide-react-native";
import { Screen } from "@/shared/ui/Screen";
import { Header } from "@/shared/ui/Header";
import { AppText } from "@/shared/ui/AppText";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { CARD_SHADOW, COLORS, RADIUS, SPACE } from "@/shared/theme/tokens";
import { EasySettingsScreen } from "./EasySettingsScreen";

// 슬라이더 위치와 레이블은 같은 인덱스로 연결되는 세 단계의 앱 내부 글자 배율이다.
const FONT_SIZES = [0.9, 1, 1.15];
const FONT_LABELS = ["작게", "보통", "크게"];
export function SettingsScreen() {
  const mode = useDisplayPreferences((state) => state.mode);
  return mode === "easy" ? <EasySettingsScreen /> : <StandardSettingsScreen />;
}
function StandardSettingsScreen() {
  const fontScale = useDisplayPreferences((state) => state.fontScale);
  const setFontScale = useDisplayPreferences((state) => state.setFontScale);
  const setMode = useDisplayPreferences((state) => state.setMode);
  const [infoOpen, setInfoOpen] = useState(false);
  return (
    <Screen
      header={<Header title="설정" back />}
      contentStyle={{ paddingTop: 18.462, gap: 22.154 }}
    >
      <View style={styles.panel}>
        <AppText variant="bold" size={16.62}>
          화면 테마
        </AppText>
        <View
          accessibilityRole="radio"
          accessibilityState={{ checked: true }}
          aria-checked={true}
          accessibilityLabel="일반 화면, 선택됨"
          style={[styles.option, { backgroundColor: COLORS.soft }]}
        >
          <Image
            source={require("@/assets/figma/settings-imgRadio.svg")}
            style={styles.radio}
          />
          <View style={{ flex: 1, gap: 3.692 }}>
            <AppText variant="bold">일반 화면</AppText>
            <AppText secondary size={11.08}>
              일반적인 크기와 다양한 기능
            </AppText>
          </View>
        </View>
        <Pressable
          accessibilityRole="radio"
          onPress={() => {
            setMode("easy");
            router.replace("/");
          }}
          accessibilityState={{ checked: false }}
          aria-checked={false}
          accessibilityLabel="편한 화면으로 변경"
          style={styles.option}
        >
          <Image
            source={require("@/assets/figma/settings-imgRadio1.svg")}
            style={styles.radio}
          />
          <View style={{ flex: 1, gap: 3.692 }}>
            <AppText variant="bold">편한 화면</AppText>
            <AppText secondary size={11.08}>
              큰 글씨와 간단한 구성
            </AppText>
          </View>
        </Pressable>
      </View>
      <View style={styles.panel}>
        <AppText variant="bold" size={16.62}>
          글자 크기
        </AppText>
        <View style={styles.fontLabels}>
          <AppText secondary size={12.92}>
            A
          </AppText>
          <AppText variant="bold">
            {FONT_LABELS[FONT_SIZES.indexOf(fontScale)]}
          </AppText>
          <AppText variant="display" secondary size={22.15}>
            A
          </AppText>
        </View>
        <View
          accessibilityRole="adjustable"
          accessibilityLabel="글자 크기"
          aria-valuemin={0}
          aria-valuemax={2}
          aria-valuenow={FONT_SIZES.indexOf(fontScale)}
          aria-valuetext={FONT_LABELS[FONT_SIZES.indexOf(fontScale)]}
          accessibilityValue={{
            min: 0,
            max: 2,
            now: FONT_SIZES.indexOf(fontScale),
            text: FONT_LABELS[FONT_SIZES.indexOf(fontScale)],
          }}
          accessibilityActions={[
            { name: "increment", label: "글자 크게" },
            { name: "decrement", label: "글자 작게" },
          ]}
          onAccessibilityAction={(event) =>
            setFontScale(
              FONT_SIZES[
                Math.max(
                  0,
                  Math.min(
                    2,
                    FONT_SIZES.indexOf(fontScale) +
                      (event.nativeEvent.actionName === "increment" ? 1 : -1),
                  ),
                )
              ],
            )
          }
          style={styles.track}
        >
          <View pointerEvents="none" style={styles.trackLine} />
          <View
            pointerEvents="none"
            style={[
              styles.progress,
              { width: `${FONT_SIZES.indexOf(fontScale) * 50}%` },
            ]}
          />
          {FONT_SIZES.map((size, index) => (
            <Pressable
              key={size}
              accessibilityRole="button"
              accessibilityLabel={`글자 크기 ${FONT_LABELS[index]}`}
              accessibilityState={{ selected: fontScale === size }}
              aria-pressed={fontScale === size}
              onPress={() => setFontScale(size)}
              style={[styles.stop, { left: `${index * 50}%` }]}
            >
              {fontScale === size && (
                <Image
                  source={require("@/assets/figma/settings-imgThumb.svg")}
                  style={{ width: 27.6923, height: 27.6923 }}
                />
              )}
            </Pressable>
          ))}
        </View>
      </View>
      <View style={styles.panel}>
        <AppText variant="bold" size={16.62}>
          기타
        </AppText>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="앱 정보 보기"
          onPress={() => setInfoOpen(true)}
          style={styles.info}
        >
          <Image
            source={require("@/assets/figma/settings-imgIconInfo.svg")}
            style={styles.radio}
          />
          <AppText style={{ flex: 1 }}>앱 정보</AppText>
          <ChevronRight color={COLORS.secondary} size={18} strokeWidth={1.5} />
        </Pressable>
      </View>
      <Modal
        visible={infoOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setInfoOpen(false)}
      >
        <View style={styles.overlay}>
          <View accessibilityViewIsModal style={styles.modal}>
            <AppText variant="display" size={24}>
              월계알리미
            </AppText>
            <AppText>우리 동네의 중요한 공문을 더 가까이, 더 쉽게</AppText>
            <AppText secondary size={12.92}>
              버전 1.0.0 · 화면 검토용 UI{"\n"}실제 서버 연동은 준비 중입니다.
              {"\n"}화면 방식과 글자 크기는 다음 실행에도 유지됩니다.
            </AppText>
            <Pressable
              accessibilityRole="button"
              onPress={() => setInfoOpen(false)}
              style={styles.close}
            >
              <AppText style={{ color: COLORS.primary }}>닫기</AppText>
            </Pressable>
          </View>
        </View>
      </Modal>
    </Screen>
  );
}
const styles = StyleSheet.create({
  panel: {
    padding: SPACE.xl,
    borderWidth: 0.923,
    borderColor: COLORS.border,
    borderRadius: RADIUS.card,
    backgroundColor: COLORS.surface,
    gap: 14.769,
    ...CARD_SHADOW,
  },
  option: {
    flexDirection: "row",
    gap: 11.077,
    alignItems: "center",
    padding: 11.077,
    borderRadius: 12,
  },
  radio: { width: 18.4615, height: 18.4615 },
  fontLabels: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  track: { height: 44, marginHorizontal: 11 },
  trackLine: {
    position: "absolute",
    top: 20,
    left: 0,
    right: 0,
    height: 3.692,
    borderRadius: 2,
    backgroundColor: COLORS.border,
  },
  progress: {
    position: "absolute",
    top: 20,
    left: 0,
    height: 3.692,
    borderRadius: 2,
    backgroundColor: COLORS.primary,
  },
  stop: {
    position: "absolute",
    top: 0,
    width: 44,
    height: 44,
    marginLeft: -22,
    alignItems: "center",
    justifyContent: "center",
  },
  info: {
    flexDirection: "row",
    minHeight: 44.308,
    alignItems: "center",
    gap: 11.077,
  },
  overlay: {
    flex: 1,
    backgroundColor: "rgba(36,59,83,0.4)",
    justifyContent: "center",
    padding: 24,
  },
  modal: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.card,
    padding: 24,
    gap: 20,
    maxWidth: 500,
    width: "100%",
    alignSelf: "center",
  },
  close: { minHeight: 44, alignItems: "center", justifyContent: "center" },
});
