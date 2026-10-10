import { CARD_KEYS, type SummaryCardKey } from "../domain/summaryEvidence";
import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { useFocusEffect, useLocalSearchParams } from "expo-router";
import { Bookmark } from "lucide-react-native";
import { Screen } from "@/shared/ui/Screen";
import { Header } from "@/shared/ui/Header";
import { IconButton } from "@/shared/ui/IconButton";
import { AppText } from "@/shared/ui/AppText";
import { COLORS, CARD_SHADOW, RADIUS } from "@/shared/theme/tokens";
import { CategoryBadge } from "../components/CategoryBadge";
import { NoticeState } from "../components/NoticeState";
import { useNotice } from "../hooks/useNotices";
import { useBookmarkStore } from "../store/bookmarkStore";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { EasyNoticeDetailScreen } from "./EasyNoticeDetailScreen";
import { DetailCharacter } from "@/shared/ui/character/AnimatedCharacter";
import { NoticeDocumentText } from "../components/NoticeDocumentText";
import { NoticeTermOverlay } from "../components/NoticeTermOverlay";
import { NoticeOmissions } from "../components/NoticeOmissions";
import { NoticeFiles } from "../components/NoticeFiles";
import { NoticeSummaryStatus } from "../components/NoticeSummaryStatus";
import { type GlossaryTerm } from "../types/notice";
import { formatSummaryText } from "../domain/noticePresentation";

const SUMMARY_ICONS = [
  require("@/assets/figma/detail-imgIconSummaryCalendar.svg"),
  require("@/assets/figma/detail-imgIconSummaryUser.svg"),
  require("@/assets/figma/detail-imgIconSummaryChecklist.svg"),
  require("@/assets/figma/detail-imgIconSummaryAlertCircle.svg"),
];
export function NoticeDetailScreen() {
  const mode = useDisplayPreferences((state) => state.mode);
  const { id } = useLocalSearchParams<{ id: string }>();
  return mode === "easy" ? (
    <EasyNoticeDetailScreen key={id} />
  ) : (
    <StandardNoticeDetailScreen key={id} />
  );
}
function StandardNoticeDetailScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const id = typeof params.id === "string" ? params.id : "";
  const query = useNotice(id);
  return (
    <StandardNoticeContent
      key={`${id}:${query.data?.hasEasyText === true}`}
      id={id}
      query={query}
    />
  );
}
function StandardNoticeContent({
  id,
  query,
}: {
  id: string;
  query: ReturnType<typeof useNotice>;
}) {
  const [animationActive, setAnimationActive] = useState(false);
  useFocusEffect(
    useCallback(() => {
      setAnimationActive(true);
      return () => setAnimationActive(false);
    }, []),
  );
  const saved = useBookmarkStore((state) => state.savedIds.includes(id));
  const toggleBookmark = useBookmarkStore((state) => state.toggleBookmark);
  // 서버가 제공한 쉬운말만 표시하며 이 화면에서는 변환 요청을 실행하지 않는다.
  const [easyRequested, setEasy] = useState(false);
  const [term, setTerm] = useState<GlossaryTerm | null>(null);
  const notice = query.data;
  const [selection, setSelection] = useState<{
    card: SummaryCardKey;
    notice: typeof notice;
  } | null>(null);
  const selectedCard =
    selection?.notice === notice ? selection?.card : undefined;
  const selectCard = (label: string) => {
    const card = CARD_KEYS[label];
    setTerm(null);
    setEasy(false);
    setSelection(selectedCard === card ? null : { card, notice });
  };
  const easy = easyRequested && notice?.hasEasyText === true;
  const rows = notice
    ? [
        ["기한", notice.deadline],
        ["대상", notice.audience],
        ["할 일", notice.task],
        ["유의사항", notice.caution],
      ].filter(([, value]) => Boolean(value))
    : [];
  return (
    <Screen
      headerBehavior="scroll"
      contentStyle={{ paddingHorizontal: 20, gap: 20 }}
      overlay={
        <NoticeTermOverlay
          term={notice?.hasEasyText ? term : null}
          easy={easy}
          onClose={() => setTerm(null)}
        />
      }
      header={
        <Header
          title=""
          back
          right={
            notice ? (
              <IconButton
                accessibilityLabel={saved ? "공문 보관 해제" : "공문 보관하기"}
                accessibilityState={{ selected: saved }}
                aria-pressed={saved}
                onPress={() => toggleBookmark(id)}
              >
                <Bookmark
                  size={22}
                  color={saved ? COLORS.primary : COLORS.secondary}
                  fill={saved ? COLORS.primary : "none"}
                  strokeWidth={1.5}
                />
              </IconButton>
            ) : undefined
          }
        />
      }
    >
      {query.isError && query.data && (
        <NoticeState
          error
          errorDetail={query.error}
          retrying={query.isFetching}
          retry={() => {
            void query.refetch();
          }}
        />
      )}
      {query.isPending || (query.isError && !query.data) ? (
        <NoticeState
          loading={query.isPending}
          error={query.isError}
          errorDetail={query.error}
          retrying={query.isFetching}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : !notice ? (
        <NoticeState message="공문을 찾을 수 없어요." />
      ) : (
        <>
          <View style={styles.metadata}>
            <CategoryBadge>{notice.category}</CategoryBadge>
            <AppText
              secondary
              size={14}
              lineHeight={22}
              style={{ flexShrink: 1 }}
            >
              {notice.provider} · 공고 {notice.publishedAt}
            </AppText>
          </View>
          <View style={styles.titleRow}>
            <AppText
              variant="display"
              size={28}
              lineHeight={38}
              style={{ flex: 1 }}
            >
              {notice.title}
            </AppText>
            <DetailCharacter active={animationActive} />
          </View>
          <NoticeSummaryStatus status={notice.summaryStatus} />
          {rows.length > 0 && (
            <View style={styles.summary}>
              <AppText variant="bold" size={18} lineHeight={27}>
                핵심만 먼저 확인해요
              </AppText>
              {rows.map(([label, value], index) => (
                <Pressable
                  key={label}
                  style={styles.summaryRow}
                  accessibilityRole="button"
                  accessibilityLabel={`${label}: ${value}. 원문 근거 강조`}
                  accessibilityState={{
                    selected: selectedCard === CARD_KEYS[label],
                  }}
                  onPress={() => selectCard(label)}
                >
                  <View style={styles.iconBadge}>
                    <Image
                      source={SUMMARY_ICONS[index]}
                      style={{ width: 28, height: 28 }}
                    />
                  </View>
                  <View style={{ flex: 1, gap: 4 }}>
                    <AppText variant="bold" size={16} lineHeight={26}>
                      {label}
                    </AppText>
                    <AppText size={16} lineHeight={26}>
                      {formatSummaryText(value)}
                    </AppText>
                  </View>
                </Pressable>
              ))}
            </View>
          )}
          <View style={styles.document}>
            <View
              style={styles.segment}
              accessibilityRole="tablist"
              accessibilityLabel="공문 읽기 방식"
            >
              {[false, true].map((mode) => (
                <Pressable
                  key={String(mode)}
                  accessibilityRole="tab"
                  accessibilityLabel={mode ? "쉬운말" : "원문"}
                  accessibilityState={{
                    selected: easy === mode,
                    disabled: mode && !notice.hasEasyText,
                  }}
                  disabled={mode && !notice.hasEasyText}
                  aria-selected={easy === mode}
                  onPress={() => {
                    setTerm(null);
                    setEasy(mode);
                    setSelection(null);
                  }}
                  style={[
                    styles.segmentItem,
                    easy === mode && styles.segmentSelected,
                  ]}
                >
                  <AppText
                    variant="bold"
                    size={16}
                    lineHeight={24}
                    style={{
                      color: easy === mode ? COLORS.surface : COLORS.secondary,
                    }}
                  >
                    {mode ? "쉬운말" : "원문"}
                  </AppText>
                </Pressable>
              ))}
            </View>
            <AppText secondary size={12} lineHeight={18}>
              {!notice.hasEasyText
                ? "쉬운말이 아직 준비되지 않았어요. 원문으로 확인해 주세요."
                : easy
                  ? "점선 표현을 누르면 원문 단어를 볼 수 있어요."
                  : "밑줄 친 단어를 누르면 뜻을 볼 수 있어요."}
            </AppText>
            <AppText variant="bold" size={18} lineHeight={27}>
              {notice.documentTitle}
            </AppText>
            {easy && !notice.easyAttachmentContentIncluded && (
              <AppText secondary size={12} lineHeight={18}>
                첨부 파일 내용은 쉬운말에 포함되지 않아요. 파일을 따로 확인해
                주세요.
              </AppText>
            )}
            <NoticeDocumentText
              text={
                (easy ? notice.easy : notice.original) ||
                "본문 텍스트가 없어요. 아래 이미지 또는 공식 원문을 확인해 주세요."
              }
              parts={
                easy
                  ? notice.documentParts?.easy
                  : notice.documentParts?.original
              }
              terms={notice.terms}
              easy={easy}
              highlights={
                !easy && selectedCard
                  ? notice.summaryEvidence?.[selectedCard]
                  : undefined
              }
              onTermPress={setTerm}
            />
          </View>
          <NoticeOmissions notice={notice} />
          <NoticeFiles files={notice.files} sourceUrl={notice.sourceUrl} />
          <View style={{ gap: 4, paddingTop: 12 }}>
            <AppText secondary size={12} lineHeight={18}>
              월계알리미는 노원구청의 공식 서비스가 아닙니다.
            </AppText>
            <AppText secondary size={12} lineHeight={18}>
              공개된 공지 정보를 모아 쉽게 전달해요.
            </AppText>
          </View>
        </>
      )}
    </Screen>
  );
}
const styles = StyleSheet.create({
  metadata: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
  },
  titleRow: {
    flexDirection: "row",
    gap: 11.077,
    alignItems: "center",
    minHeight: 117,
  },
  summary: {
    backgroundColor: COLORS.soft,
    borderRadius: RADIUS.card,
    paddingHorizontal: 32,
    paddingVertical: 24,
    gap: 20,
    ...CARD_SHADOW,
  },
  summaryRow: { flexDirection: "row", gap: 16, alignItems: "center" },
  iconBadge: {
    width: 40,
    height: 40,
    borderRadius: RADIUS.control,
    backgroundColor: COLORS.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  document: {
    padding: 20,
    borderColor: COLORS.border,
    borderWidth: 0.923,
    borderRadius: RADIUS.card,
    gap: 12,
  },
  segment: {
    flexDirection: "row",
    backgroundColor: COLORS.soft,
    borderRadius: RADIUS.pill,
    padding: 4,
  },
  segmentItem: {
    flex: 1,
    minHeight: 48,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: RADIUS.pill,
    alignItems: "center",
    justifyContent: "center",
  },
  segmentSelected: {
    backgroundColor: COLORS.primary,
    shadowColor: COLORS.text,
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 1,
  },
});
