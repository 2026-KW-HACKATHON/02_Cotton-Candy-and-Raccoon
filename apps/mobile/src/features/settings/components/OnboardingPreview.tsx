import { type ReactNode, useRef, useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { Bookmark, ChevronDown, Settings, X } from "lucide-react-native";
import { Image } from "expo-image";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { type DisplayMode } from "@/shared/accessibility/displayPreferences";
import { LetterOrbit } from "@/features/notices/components/LetterOrbit";
import { NoticeDocumentText } from "@/features/notices/components/NoticeDocumentText";
import { NOTICE_PREVIEW_FIXTURES } from "@/features/notices/fixtures/noticeFixtures";
import {
  type OnboardingStep,
  type OnboardingTarget,
} from "../domain/onboardingSteps";

const NOTICE = NOTICE_PREVIEW_FIXTURES[0];
const BLUE = "#0F99FF";
const NAV_IMAGES = [
  require("@/assets/figma/home-imgIconNavigationStackedEnvelopes.svg"),
  require("@/assets/figma/home-imgIconNavigationHome.svg"),
  require("@/assets/figma/home-imgIconNavigationArchiveBox.svg"),
];

export function PreviewButton({
  label,
  onPress,
  selected = true,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  selected?: boolean;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={disabled ? undefined : onPress}
      style={[styles.button, !selected && styles.secondary]}
    >
      <AppText
        variant="bold"
        size={16}
        style={{
          textAlign: "center",
          color: selected ? COLORS.surface : COLORS.primary,
        }}
      >
        {label}
      </AppText>
    </Pressable>
  );
}

// 첫 안내는 서버 상태에 의존하지 않고, 명시적인 예시로 구성해 실제 저장·외부 이동을 수행하지 않는다.
export function OnboardingPreview({
  mode,
  step,
  progress,
  onTry,
}: {
  mode: DisplayMode;
  step?: OnboardingStep;
  progress?: string;
  onTry: () => void;
}) {
  const scroll = useRef<ScrollView>(null);
  const [positions, setPositions] = useState<
    Partial<Record<OnboardingTarget, number>>
  >({});
  const easy = mode === "easy";
  const detail = step?.scene === "detail";
  const size = easy ? 20 : 16;
  const orbitTarget = step?.target === "carousel" ? "carousel" : "open";
  const isActive = (target: OnboardingTarget) => step?.target === target;
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (step)
        scroll.current?.scrollTo({
          y: Math.max(0, (positions[step.target] ?? 0) - 12),
          animated: false,
        });
      else scroll.current?.scrollTo({ y: 0, animated: false });
    });
    return () => cancelAnimationFrame(frame);
  }, [step, mode, positions]);
  function section(target: OnboardingTarget, children: ReactNode) {
    const active = step?.target === target;
    return (
      <View
        key={target}
        onLayout={(event) => {
          const y = event.nativeEvent.layout.y;
          setPositions((values) =>
            values[target] === y ? values : { ...values, [target]: y },
          );
        }}
        style={styles.section}
      >
        {active && (
          <View accessibilityLiveRegion="polite" style={styles.guide}>
            <AppText variant="bold" size={12} style={{ color: BLUE }}>
              {step.label} · {progress}
            </AppText>
            <AppText variant="bold" size={20}>
              {step.title}
            </AppText>
            <AppText size={16}>{step.description}</AppText>
          </View>
        )}
        {active && (
          <View style={styles.connector}>
            <View style={styles.dot} />
          </View>
        )}
        <View
          pointerEvents={!step || !active ? "none" : "auto"}
          accessibilityElementsHidden={!step || !active}
          importantForAccessibility={
            !step || !active ? "no-hide-descendants" : "auto"
          }
          aria-hidden={!step || !active}
          style={[active && styles.highlight, !!step && !active && styles.dim]}
        >
          {children}
        </View>
      </View>
    );
  }
  return (
    <ScrollView
      ref={scroll}
      showsVerticalScrollIndicator={false}
      contentContainerStyle={[
        styles.content,
        easy && { backgroundColor: COLORS.soft },
      ]}
      keyboardShouldPersistTaps="handled"
    >
      <AppText secondary size={12}>
        사용 안내 · 예시 공문
      </AppText>
      {section(
        detail ? "save" : "settings",
        <View style={styles.header}>
          <AppText variant="display" size={28}>
            {detail ? "공문 상세" : "월계알리미"}
            <AppText size={28} style={{ color: "#F26454" }}>
              .
            </AppText>
          </AppText>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={
              detail ? "예시 공문 저장하기" : "설정 안내 보기"
            }
            disabled={!isActive(detail ? "save" : "settings")}
            onPress={isActive(detail ? "save" : "settings") ? onTry : undefined}
            style={styles.icon}
          >
            {detail ? (
              <Bookmark color={COLORS.primary} size={24} />
            ) : (
              <Settings color={COLORS.secondary} size={24} />
            )}
            {detail && easy && (
              <AppText variant="bold" size={16}>
                저장하기
              </AppText>
            )}
          </Pressable>
        </View>,
      )}
      {!detail ? (
        <>
          <View style={step && styles.dim}>
            <AppText variant={easy ? "body" : "display"} size={easy ? 20 : 32}>
              {easy
                ? "새로 등록된 공문을 확인해 주세요."
                : "오늘의 공문이 도착했어요"}
            </AppText>
          </View>
          {section(
            "scope",
            easy ? (
              <View style={{ gap: 12 }}>
                <AppText variant="bold" size={20}>
                  현재 출처 · 월계1동
                </AppText>
                {["서울시", "노원구", "월계1동"].map((label) => (
                  <PreviewButton
                    key={label}
                    label={label}
                    selected={label === "월계1동"}
                    disabled={!isActive("scope")}
                    onPress={onTry}
                  />
                ))}
              </View>
            ) : (
              <View style={styles.row}>
                <AppText secondary size={13}>
                  최근 공문 · {NOTICE.publishedAt}
                </AppText>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="지역 선택 안내"
                  disabled={!isActive("scope")}
                  onPress={isActive("scope") ? onTry : undefined}
                  style={[styles.icon, styles.card]}
                >
                  <AppText>월계1동</AppText>
                  <ChevronDown size={18} color={COLORS.secondary} />
                </Pressable>
              </View>
            ),
          )}
          {section(
            orbitTarget,
            easy ? (
              <View style={[styles.card, { gap: 12 }]}>
                <AppText variant="bold" size={22}>
                  {NOTICE.title}
                </AppText>
                <AppText size={20}>{NOTICE.description}</AppText>
                <AppText secondary size={20}>
                  게시일 {NOTICE.publishedAt}
                  {"\n"}정보제공처 {NOTICE.provider}
                </AppText>
                <PreviewButton
                  label="공문 보기"
                  disabled={!isActive(orbitTarget)}
                  onPress={onTry}
                />
              </View>
            ) : (
              <View style={{ marginHorizontal: -20 }}>
                <LetterOrbit
                  notices={[...NOTICE_PREVIEW_FIXTURES.slice(0, 3)]}
                  interactive={isActive(orbitTarget)}
                  onOpen={onTry}
                />
              </View>
            ),
          )}
          <View
            style={step && step.target !== "carousel" && styles.dim}
            pointerEvents={step?.target === "carousel" ? "auto" : "none"}
            accessibilityElementsHidden={!isActive("carousel")}
            importantForAccessibility={
              isActive("carousel") ? "auto" : "no-hide-descendants"
            }
            aria-hidden={!isActive("carousel")}
          >
            <PreviewButton
              label="전체 공문 보기  ›"
              disabled={!isActive("carousel")}
              onPress={onTry}
            />
          </View>
          {section(
            "navigation",
            <View style={[styles.row, styles.navigation]}>
              {(easy
                ? ["홈", "전체 공문", "저장한 공문", "설정"]
                : ["전체 공문", "홈", "저장한 공문"]
              ).map((label, index) => (
                <Pressable
                  key={label}
                  accessibilityRole="button"
                  accessibilityLabel={label + " 안내"}
                  disabled={!isActive("navigation")}
                  onPress={isActive("navigation") ? onTry : undefined}
                  style={styles.navItem}
                >
                  {easy ? (
                    <AppText size={18} style={{ textAlign: "center" }}>
                      {label}
                    </AppText>
                  ) : (
                    <Image
                      source={NAV_IMAGES[index]}
                      style={{ width: 30, height: 30 }}
                    />
                  )}
                </Pressable>
              ))}
            </View>,
          )}
        </>
      ) : (
        <>
          <View style={step && styles.dim}>
            <AppText variant="bold" size={easy ? 28 : 24}>
              {NOTICE.title}
            </AppText>
            <AppText secondary size={14}>
              {NOTICE.provider} · {NOTICE.publishedAt}
            </AppText>
          </View>
          {section(
            "summary",
            <View style={[styles.card, { gap: 16 }]}>
              <AppText variant="display" size={easy ? 28 : 24}>
                핵심 내용
              </AppText>
              {[
                ["대상", NOTICE.audience],
                ["할 일", NOTICE.task],
                ["기한", NOTICE.deadline],
                ["주의", NOTICE.caution],
              ].map(([label, value]) => (
                <View key={label} style={{ gap: 4 }}>
                  <AppText
                    variant="bold"
                    size={size}
                    style={{ color: COLORS.primary }}
                  >
                    {label}
                  </AppText>
                  <AppText size={size}>{value}</AppText>
                </View>
              ))}
            </View>,
          )}
          {section(
            "reading",
            <View style={[styles.card, { gap: 16 }]}>
              <View style={styles.row}>
                <PreviewButton
                  label="원문"
                  disabled={!isActive("reading")}
                  onPress={onTry}
                />
                <PreviewButton
                  label="쉬운말"
                  selected={false}
                  disabled={!isActive("reading")}
                  onPress={onTry}
                />
              </View>
              <NoticeDocumentText
                text={NOTICE.original}
                terms={NOTICE.terms}
                easy={false}
                comfortable={easy}
                interactive={isActive("reading")}
                onTermPress={onTry}
              />
            </View>,
          )}
          {step?.target === "word" &&
            section(
              "word",
              <View style={[styles.card, { gap: 16 }]}>
                <View style={styles.row}>
                  <AppText variant="display" size={24}>
                    단어 뜻
                  </AppText>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="단어 뜻 닫고 다음 안내 보기"
                    disabled={!isActive("word")}
                    onPress={isActive("word") ? onTry : undefined}
                    style={styles.icon}
                  >
                    <X color={COLORS.secondary} size={24} />
                  </Pressable>
                </View>
                <AppText variant="bold" size={22}>
                  선착순
                </AppText>
                <AppText size={16}>
                  먼저 신청한 사람부터 차례로 뽑는다는 뜻이에요.
                </AppText>
                <AppText variant="bold" size={16}>
                  예시
                </AppText>
                <AppText size={16}>
                  먼저 신청한 20명이 문화교실에 참여할 수 있어요.
                </AppText>
              </View>,
            )}
          {section(
            "source",
            <PreviewButton
              label="원문 파일 보기"
              disabled={!isActive("source")}
              onPress={onTry}
            />,
          )}
        </>
      )}
      <AppText
        secondary
        size={12}
        style={[{ textAlign: "center" }, step && styles.dim]}
      >
        이 공문은 사용 설명을 위한 예시입니다.
      </AppText>
    </ScrollView>
  );
}
const styles = StyleSheet.create({
  content: { padding: 20, gap: 16, paddingBottom: 40 },
  section: { gap: 0 },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
  },
  icon: {
    minHeight: 48,
    minWidth: 48,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
  },
  button: {
    minHeight: 52,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 12,
    backgroundColor: COLORS.primary,
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 1,
  },
  secondary: {
    backgroundColor: "#FFFFFF",
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  card: {
    padding: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: 20,
    backgroundColor: COLORS.surface,
  },
  navigation: {
    padding: 8,
    backgroundColor: COLORS.surface,
    borderRadius: 32,
    minHeight: 72,
  },
  navItem: {
    flex: 1,
    minHeight: 52,
    alignItems: "center",
    justifyContent: "center",
  },
  guide: {
    gap: 8,
    padding: 20,
    borderWidth: 2,
    borderColor: BLUE,
    borderRadius: 16,
    backgroundColor: COLORS.surface,
    shadowColor: COLORS.text,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.16,
    shadowRadius: 12,
    elevation: 4,
  },
  highlight: {
    borderWidth: 3,
    borderColor: BLUE,
    borderRadius: 12,
    overflow: "hidden",
  },
  dim: { opacity: 0.45 },
  connector: {
    height: 32,
    width: 2,
    borderLeftWidth: 2,
    borderStyle: "dashed",
    borderColor: BLUE,
    alignSelf: "center",
  },
  dot: {
    position: "absolute",
    bottom: -6,
    left: -7,
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: BLUE,
  },
});
