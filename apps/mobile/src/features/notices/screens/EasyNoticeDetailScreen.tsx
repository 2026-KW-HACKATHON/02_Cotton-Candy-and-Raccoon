import { CARD_KEYS, type SummaryCardKey } from "../domain/summaryEvidence";
import {
  currentDictionaryTerm,
  dictionaryHint,
} from "../domain/noticeDictionary";
import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { useLocalSearchParams } from "expo-router";
import { Screen } from "@/shared/ui/Screen";
import { goBack } from "@/shared/ui/Header";
import { AppText } from "@/shared/ui/AppText";
import { EasyButton } from "@/shared/ui/EasyButton";
import { COLORS, EASY } from "@/shared/theme/tokens";
import { useNotice } from "../hooks/useNotices";
import { useBookmarkStore } from "../store/bookmarkStore";
import { EasyNoticeState } from "../components/EasyNoticeState";
import { NoticeDocumentText } from "../components/NoticeDocumentText";
import { NoticeTermOverlay } from "../components/NoticeTermOverlay";
import { NoticeOmissions } from "../components/NoticeOmissions";
import { NoticeFiles } from "../components/NoticeFiles";
import { NoticeSummaryStatus } from "../components/NoticeSummaryStatus";
import {
  formatSummaryText,
  getSummaryRows,
  isNoticeExpired,
} from "../domain/noticePresentation";
import { type GlossaryTerm } from "../types/notice";

// Figma QYCEBzvJCSX22QZ1VmJn8Q, 460:955/1231 및 연결 오버레이, 조회 2026-10-08.
export function EasyNoticeDetailScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const id = typeof params.id === "string" ? params.id : "";
  const query = useNotice(id);
  const saved = useBookmarkStore((state) => state.savedIds.includes(id));
  const toggleBookmark = useBookmarkStore((state) => state.toggleBookmark);
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
  // 재조회로 쉬운말이 무효화되면 즉시 원문을 표시하고 이전 선택과 단어 설명을 해제한다.
  const easy = easyRequested && notice?.hasEasyText === true;
  if (easyRequested && notice && !notice.hasEasyText) {
    setEasy(false);
    setTerm(null);
  }
  const rows = notice ? getSummaryRows(notice) : [];
  return (
    <Screen
      backgroundColor={COLORS.surface}
      bottomSafe
      overlay={
        <NoticeTermOverlay
          comfortable
          term={currentDictionaryTerm(notice, term)}
          easy={easy}
          onClose={() => setTerm(null)}
        />
      }
      header={
        <View style={styles.actions}>
          <EasyButton
            label="이전 화면으로"
            icon={
              <Image
                source={require("@/assets/figma/easy-detail/back.svg")}
                style={{ width: 16, height: 24 }}
              />
            }
            onPress={goBack}
            style={{ flex: 1 }}
          />
          <EasyButton
            label={saved ? "저장 취소" : "저장하기"}
            filled
            selected={saved}
            disabled={!notice}
            onPress={() => toggleBookmark(id)}
            style={{ flex: 1 }}
          />
        </View>
      }
      contentStyle={{
        paddingHorizontal: EASY.inset,
        paddingTop: EASY.inset,
        gap: EASY.gap,
      }}
    >
      {query.isError && query.data && (
        <EasyNoticeState
          error
          errorDetail={query.error}
          retrying={query.isFetching}
          retry={() => {
            void query.refetch();
          }}
        />
      )}
      {query.isPending || (query.isError && !query.data) ? (
        <EasyNoticeState
          loading={query.isPending}
          error={query.isError}
          errorDetail={query.error}
          retrying={query.isFetching}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : !notice ? (
        <EasyNoticeState message="공문을 찾을 수 없습니다" />
      ) : (
        <>
          <View style={{ gap: 12 }}>
            <AppText size={EASY.title} variant="bold">
              {notice.title}
            </AppText>
            <AppText
              size={EASY.body}
              secondary
            >{`공고 ${notice.publishedAt.replaceAll(" ", "")}\n정보제공처 ${notice.provider}`}</AppText>
          </View>
          <NoticeSummaryStatus comfortable status={notice.summaryStatus} />
          {isNoticeExpired(notice) && (
            <View style={styles.card}>
              <AppText
                size={EASY.body}
                variant="bold"
                style={{ color: EASY.expired }}
              >
                종료된 공문 · 신청기한{"\n"}
                {notice.deadline}
              </AppText>
            </View>
          )}
          {rows.length > 0 && (
            <View
              style={[styles.card, { backgroundColor: COLORS.soft, gap: 12 }]}
            >
              <AppText size={EASY.heading} variant="bold">
                핵심 내용
              </AppText>
              {rows.map((row) => (
                <Pressable
                  key={row.label}
                  style={{ gap: 4 }}
                  accessibilityRole="button"
                  accessibilityLabel={`${row.label}: ${row.value}. 원문 근거 강조`}
                  accessibilityState={{
                    selected: selectedCard === CARD_KEYS[row.label],
                  }}
                  onPress={() => selectCard(row.label)}
                >
                  <AppText
                    size={EASY.body}
                    variant="bold"
                    style={{ color: COLORS.primary }}
                  >
                    {row.label}
                  </AppText>
                  <AppText size={EASY.body}>
                    {formatSummaryText(row.value)}
                  </AppText>
                </Pressable>
              ))}
            </View>
          )}
          <View style={[styles.card, { gap: 16 }]}>
            <AppText size={EASY.heading} variant="bold">
              {easy ? "쉬운말 공문" : "공문 원문"}
            </AppText>
            <EasyButton
              label={easy ? "원문으로 읽기" : "쉬운말로 읽기"}
              selected={easy}
              disabled={!easy && !notice.hasEasyText}
              onPress={() => {
                setTerm(null);
                setEasy((value) => !value);
                setSelection(null);
              }}
            />
            <AppText size={EASY.body} secondary>
              {easy
                ? notice.easyIsRewrite
                  ? "본문을 읽기 쉽게 다시 썼어요. 정확한 내용은 원문도 확인해 주세요."
                  : "점선 표현을 누르면 원문 단어를 볼 수 있어요."
                : dictionaryHint(notice)}
            </AppText>
            {easy && !notice.easyAttachmentContentIncluded && (
              <AppText size={EASY.body} secondary>
                첨부 파일 내용은 쉬운말에 포함되지 않아요. 파일을 따로 확인해
                주세요.
              </AppText>
            )}
            <NoticeDocumentText
              comfortable
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
          <NoticeOmissions notice={notice} comfortable />
          <NoticeFiles
            comfortable
            files={notice.files}
            sourceUrl={notice.sourceUrl}
          />
          <AppText size={EASY.body} secondary>
            {
              "월계알리미는 노원구청의 공식 서비스가 아닙니다.\n공개된 공지 정보를 모아 쉽게 전달해요."
            }
          </AppText>
        </>
      )}
    </Screen>
  );
}
const styles = StyleSheet.create({
  actions: {
    flexDirection: "row",
    paddingHorizontal: 8,
    paddingVertical: 12,
    gap: 8,
  },
  card: {
    padding: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: EASY.cardRadius,
    backgroundColor: COLORS.surface,
  },
});
