import { useEffect, useState } from "react";
import {
  BackHandler,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { router } from "expo-router";
import { AppText } from "@/shared/ui/AppText";
import {
  type DisplayMode,
  useDisplayPreferences,
} from "@/shared/accessibility/displayPreferences";
import { COLORS } from "@/shared/theme/tokens";
import {
  OnboardingPreview,
  PreviewButton,
} from "../components/OnboardingPreview";
import {
  EASY_ONBOARDING,
  STANDARD_ONBOARDING,
} from "../domain/onboardingSteps";

// Figma QYCEBzvJCSX22QZ1VmJn8Q, 482:11441 / 483:324・451, 조회 2026-10-09.
export function OnboardingScreen() {
  const currentMode = useDisplayPreferences((state) => state.mode);
  const complete = useDisplayPreferences((state) => state.onboardingComplete);
  const completeOnboarding = useDisplayPreferences(
    (state) => state.completeOnboarding,
  );
  const [selection, setSelection] = useState<DisplayMode>(
    complete ? currentMode : "easy",
  );
  const [step, setStep] = useState(-1);
  const steps = selection === "easy" ? EASY_ONBOARDING : STANDARD_ONBOARDING;
  const item = step >= 0 ? steps[step] : undefined;
  function finish() {
    completeOnboarding(selection);
    router.replace("/");
  }
  function next() {
    if (step === steps.length - 1) finish();
    else setStep((value) => value + 1);
  }
  useEffect(() => {
    if (Platform.OS !== "android") return;
    const subscription = BackHandler.addEventListener(
      "hardwareBackPress",
      () => {
        if (step >= 0) {
          setStep((value) => value - 1);
          return true;
        }
        return false;
      },
    );
    return () => subscription.remove();
  }, [step]);
  return (
    <SafeAreaView
      style={styles.safe}
      edges={["top", "bottom", "left", "right"]}
    >
      <View style={styles.width}>
        <View style={styles.preview}>
          <OnboardingPreview
            key={selection + (item?.scene ?? "home")}
            mode={selection}
            step={item}
            progress={`${step + 1} / ${steps.length}`}
            onTry={item ? next : () => {}}
          />
        </View>
        <ScrollView
          style={styles.footer}
          contentContainerStyle={styles.footerContent}
          bounces={false}
        >
          {item ? (
            <View style={styles.actions}>
              <View style={{ flex: 1 }}>
                <PreviewButton
                  label="이전으로"
                  selected={false}
                  onPress={() => setStep((value) => value - 1)}
                />
              </View>
              <View style={{ flex: 1 }}>
                <PreviewButton
                  label={step === steps.length - 1 ? "시작하기" : "다음으로"}
                  onPress={next}
                />
              </View>
            </View>
          ) : (
            <>
              <AppText size={14}>나에게 편한 화면을 골라 주세요</AppText>
              <View
                accessibilityRole="radiogroup"
                accessibilityLabel="화면 방식 선택"
                style={styles.actions}
              >
                {(["easy", "standard"] as const).map((mode) => (
                  <Pressable
                    key={mode}
                    accessibilityRole="radio"
                    accessibilityState={{ checked: selection === mode }}
                    onPress={() => setSelection(mode)}
                    style={[styles.mode, selection === mode && styles.selected]}
                  >
                    <AppText
                      variant="bold"
                      size={16}
                      style={{
                        color:
                          selection === mode ? COLORS.surface : COLORS.text,
                      }}
                    >
                      {mode === "easy" ? "편한화면" : "일반화면"}
                    </AppText>
                  </Pressable>
                ))}
              </View>
              <AppText size={14} accessibilityLiveRegion="polite">
                {selection === "easy"
                  ? "큰 글씨와 버튼으로 또렷하게 읽어요."
                  : "그림과 함께 오늘의 공문을 살펴봐요."}
              </AppText>
              <PreviewButton
                label="이 화면으로 시작하기"
                onPress={() => setStep(0)}
              />
            </>
          )}
        </ScrollView>
      </View>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.surface },
  width: { flex: 1, width: "100%", maxWidth: 600, alignSelf: "center" },
  preview: { flex: 1 },
  footer: {
    flexGrow: 0,
    flexShrink: 0,
    maxHeight: "45%",
    backgroundColor: COLORS.surface,
  },
  footerContent: {
    paddingHorizontal: 24,
    paddingTop: 20,
    paddingBottom: 24,
    gap: 8,
  },
  actions: { flexDirection: "row", gap: 8 },
  mode: {
    flex: 1,
    minHeight: 44,
    padding: 8,
    borderRadius: 12,
    backgroundColor: COLORS.soft,
    alignItems: "center",
    justifyContent: "center",
  },
  selected: { backgroundColor: COLORS.primary },
});
