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
import { NoticeLinks } from "../components/NoticeLinks";
import {
  NoticeReadStatus,
  NoticeQueryFeedback,
} from "../components/NoticeReadStatus";
import { useNotice } from "../hooks/useNotices";
import { useBookmarkStore } from "../store/bookmarkStore";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { EasyNoticeDetailScreen } from "./EasyNoticeDetailScreen";
import { DetailCharacter } from "@/shared/ui/character/AnimatedCharacter";
import { NoticeDocumentText } from "../components/NoticeDocumentText";
import { NoticeTermOverlay } from "../components/NoticeTermOverlay";
import { type GlossaryTerm } from "../types/notice";

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
  const [animationActive, setAnimationActive] = useState(false);
  useFocusEffect(
    useCallback(() => {
      setAnimationActive(true);
      return () => setAnimationActive(false);
    }, []),
  );
  const params = useLocalSearchParams<{ id: string }>();
  const id = typeof params.id === "string" ? params.id : "";
  const query = useNotice(id);
  const saved = useBookmarkStore((state) => state.savedIds.includes(id));
  const toggleBookmark = useBookmarkStore((state) => state.toggleBookmark);
  // DB에 저장된 결과만 표시하며 앱에서 AI를 호출하지 않는다.
  const [easy, setEasy] = useState(false);
  const [term, setTerm] = useState<GlossaryTerm | null>(null);
  const notice = query.data;
  const rows = notice
    ? [
        ["기한", notice.deadline],
        ["대상", notice.audience],
        ["할 일", notice.task],
        ["유의사항", notice.caution],
      ]
    : [];
  return (
    <Screen
      headerBehavior="scroll"
      contentStyle={{ paddingHorizontal: 20, gap: 20 }}
      overlay={
        <NoticeTermOverlay
          term={term}
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
      {query.isPending ? (
        <NoticeState
          loading={query.isPending}
          error={query.isError}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : !notice ? (
        query.isError ? (
          <NoticeQueryFeedback
            error={query.error}
            hasData={false}
            retry={() => void query.refetch()}
          />
        ) : (
          <NoticeState message="공문을 찾을 수 없어요." />
        )
      ) : (
        <>
          <NoticeQueryFeedback
            error={query.error}
            hasData={true}
            retry={() => void query.refetch()}
          />
          <NoticeReadStatus notice={notice} />
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
          <View style={styles.summary}>
            <AppText variant="bold" size={18} lineHeight={27}>
              핵심만 먼저 확인해요
            </AppText>
            {rows.map(([label, value], index) => (
              <View key={label} style={styles.summaryRow}>
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
                    {value}
                  </AppText>
                </View>
              </View>
            ))}
            <AppText secondary size={11.08}>
              AI 요약은 오류가 있을 수 있습니다. 정확한 조건은 원문을 확인해요.
            </AppText>
          </View>
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
                  accessibilityState={{ selected: easy === mode }}
                  aria-selected={easy === mode}
                  onPress={() => {
                    setTerm(null);
                    setEasy(mode);
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
              {easy
                ? "원문 표현을 누르면 원래 단어를 볼 수 있어요."
                : "밑줄 친 단어를 누르면 뜻을 볼 수 있어요."}
            </AppText>
            <AppText variant="bold" size={18} lineHeight={27}>
              {notice.documentTitle}
            </AppText>
            <NoticeDocumentText
              notice={notice}
              easy={easy}
              onTermPress={setTerm}
            />
            {easy && (!notice.hasEasyText || !notice.easy) && (
              <AppText>쉬운말 결과가 없어 원문을 표시합니다.</AppText>
            )}
            {easy &&
              notice.hasEasyText &&
              !notice.attachmentContentIncluded && (
                <AppText secondary>
                  첨부파일 내용은 쉬운말 변환에 포함되지 않았습니다.
                </AppText>
              )}
          </View>
          <NoticeLinks notice={notice} />
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
