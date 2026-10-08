import { useEffect, useState } from "react";
import { BackHandler, Platform, View } from "react-native";
import { router } from "expo-router";
import { Screen } from "@/shared/ui/Screen";
import { AppText } from "@/shared/ui/AppText";
import { EasyButton } from "@/shared/ui/EasyButton";
import {
  type DisplayMode,
  useDisplayPreferences,
} from "@/shared/accessibility/displayPreferences";
import { COLORS, EASY } from "@/shared/theme/tokens";

const STEPS = [
  {
    key: "home",
    title: "홈",
    easy: "이번 주 공문을 위에서 아래로 읽고 원하는 공문을 누르세요.",
    standard:
      "이번 주 공문을 가로로 넘겨 살펴보세요. 열린 편지를 누르면 상세를 볼 수 있습니다.",
  },
  {
    key: "notices",
    title: "전체 공문",
    easy: "전체 공문은 등록일 최신순입니다. 제목, 게시일, 정보제공처를 확인하고 누르세요.",
    standard: "제목이나 내용으로 검색하고 분류를 골라 필요한 공문을 찾으세요.",
  },
  {
    key: "saved",
    title: "저장한 공문",
    easy: "상세에서 저장한 공문을 여기에서 다시 볼 수 있습니다.",
    standard: "상세에서 보관한 공문을 보관함에서 다시 볼 수 있습니다.",
  },
  {
    key: "detail",
    title: "공문 상세",
    easy: "핵심 내용 다음에 공문 원문이 있습니다. 쉬운말로 읽기를 누르면 쉬운말 공문을 볼 수 있습니다. 점선 단어와 표현을 누르면 뜻과 원문 단어를 볼 수 있습니다.",
    standard:
      "핵심 요약을 확인하고 원문과 쉬운말을 바꿔 읽으세요. 북마크로 공문을 보관할 수 있습니다.",
  },
  {
    key: "settings",
    title: "설정",
    easy: "글자 크기와 화면 방식을 언제든 바꿀 수 있습니다.",
    standard:
      "화면 방식과 글자 크기를 바꾸고 편한 화면으로 전환할 수 있습니다.",
  },
] as const;

export function OnboardingScreen() {
  const complete = useDisplayPreferences((state) => state.onboardingComplete);
  const currentMode = useDisplayPreferences((state) => state.mode);
  const completeOnboarding = useDisplayPreferences(
    (state) => state.completeOnboarding,
  );
  const [selection, setSelection] = useState<DisplayMode | null>(
    complete ? currentMode : null,
  );
  const [step, setStep] = useState(-1);
  const [closed, setClosed] = useState(false);
  useEffect(() => {
    if (Platform.OS !== "android") return;
    const subscription = BackHandler.addEventListener(
      "hardwareBackPress",
      () => {
        if (step >= 0) {
          setStep((value) => value - 1);
          return true;
        }
        if (complete) return false;
        if (!selection && !closed) {
          setClosed(true);
          return true;
        }
        return false;
      },
    );
    return () => subscription.remove();
  }, [step, selection, closed, complete]);
  function finish() {
    if (!selection) return;
    completeOnboarding(selection);
    router.replace("/");
  }
  const item = step >= 0 ? STEPS[step] : undefined;
  return (
    <Screen
      backgroundColor={COLORS.surface}
      bottomSafe
      contentStyle={{
        paddingHorizontal: EASY.inset,
        paddingTop: EASY.inset,
        gap: EASY.gap,
      }}
    >
      {item ? (
        <>
          <View style={{ gap: 12 }}>
            <AppText size={EASY.title} variant="display">
              {selection === "easy" ? "편한 화면" : "일반 화면"} · {item.title}{" "}
              사용 설명
            </AppText>
            <AppText size={EASY.body}>
              {selection === "easy" ? item.easy : item.standard}
            </AppText>
          </View>
          <View
            style={{
              padding: 20,
              borderRadius: EASY.cardRadius,
              borderWidth: 1,
              borderColor: COLORS.border,
              backgroundColor: COLORS.soft,
            }}
          >
            <AppText size={EASY.body}>
              미리보기 · {item.key}
              {"\n"}
              {selection === "easy"
                ? "큰 글자 / 세로 목록 / 정적인 안내"
                : "기본 글자 / 다양한 기능 / 공문 탐색"}
            </AppText>
          </View>
          <EasyButton
            label="이전 사용 설명"
            onPress={() => setStep((value) => value - 1)}
          />
          <EasyButton
            label={step === STEPS.length - 1 ? "시작하기" : "다음 사용 설명"}
            filled
            onPress={() =>
              step === STEPS.length - 1
                ? finish()
                : setStep((value) => value + 1)
            }
          />
        </>
      ) : (
        <>
          <View style={{ gap: 12 }}>
            <AppText size={EASY.title} variant="display">
              어떤 화면이 편하신가요?
            </AppText>
            <AppText size={EASY.body}>
              원하는 방식을 직접 골라주세요. 설정에서 언제든 바꿀 수 있습니다.
            </AppText>
          </View>
          <AppText size={EASY.body}>
            편한 화면 · 큰 글자와 세로 목록{"\n"}정적인 설명으로 차근차근
            읽어요.
          </AppText>
          <EasyButton
            label={`편한 화면${selection === "easy" ? " · 선택됨" : ""}`}
            filled={selection === "easy"}
            selected={selection === "easy"}
            onPress={() => {
              setSelection("easy");
              setClosed(false);
            }}
          />
          <AppText size={EASY.body}>
            일반 화면 · 마스코트와 가로 목록{"\n"}이번 주 소식을 가볍게
            둘러봐요.
          </AppText>
          <EasyButton
            label={`일반 화면${selection === "standard" ? " · 선택됨" : ""}`}
            filled={selection === "standard"}
            selected={selection === "standard"}
            onPress={() => {
              setSelection("standard");
              setClosed(false);
            }}
          />
          <EasyButton
            label="사용 설명 보기"
            filled
            disabled={!selection}
            onPress={() => setStep(0)}
          />
          {!selection && (
            <AppText
              size={EASY.body}
              secondary
              accessibilityLiveRegion="polite"
            >
              {closed
                ? "선택하지 않고 종료함 · 다음 실행에 다시 선택"
                : "화면 방식을 먼저 선택해주세요"}
            </AppText>
          )}
        </>
      )}
    </Screen>
  );
}
