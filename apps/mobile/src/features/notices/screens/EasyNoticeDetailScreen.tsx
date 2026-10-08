import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { useLocalSearchParams } from "expo-router";
import { Screen } from "@/shared/ui/Screen";
import { goBack } from "@/shared/ui/Header";
import { AppText } from "@/shared/ui/AppText";
import { EasyButton } from "@/shared/ui/EasyButton";
import { COLORS, EASY } from "@/shared/theme/tokens";
import { NoticeLinks } from "../components/NoticeLinks";
import {
  NoticeReadStatus,
  NoticeQueryFeedback,
} from "../components/NoticeReadStatus";
import { useNotice } from "../hooks/useNotices";
import { useBookmarkStore } from "../store/bookmarkStore";
import { EasyNoticeState } from "../components/EasyNoticeState";
import { NoticeDocumentText } from "../components/NoticeDocumentText";
import { NoticeTermOverlay } from "../components/NoticeTermOverlay";
import { getSummaryRows } from "../domain/noticePresentation";
import { type GlossaryTerm } from "../types/notice";

// Figma QYCEBzvJCSX22QZ1VmJn8Q, 460:955/1231 및 연결 오버레이, 조회 2026-10-08.
export function EasyNoticeDetailScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const id = typeof params.id === "string" ? params.id : "";
  const query = useNotice(id);
  const saved = useBookmarkStore((state) => state.savedIds.includes(id));
  const toggleBookmark = useBookmarkStore((state) => state.toggleBookmark);
  const [easy, setEasy] = useState(false);
  const [term, setTerm] = useState<GlossaryTerm | null>(null);
  const notice = query.data;
  const rows = notice ? getSummaryRows(notice) : [];
  return (
    <Screen
      backgroundColor={COLORS.surface}
      bottomSafe
      overlay={
        <NoticeTermOverlay
          comfortable
          term={term}
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
      {query.isPending ? (
        <EasyNoticeState
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
          <EasyNoticeState message="공문을 찾을 수 없습니다" />
        )
      ) : (
        <>
          <NoticeQueryFeedback
            error={query.error}
            hasData={true}
            retry={() => void query.refetch()}
          />
          <NoticeReadStatus notice={notice} />
          <View style={{ gap: 12 }}>
            <AppText size={EASY.title} variant="bold">
              {notice.title}
            </AppText>
            <AppText
              size={EASY.body}
              secondary
            >{`공고 ${notice.publishedAt.replaceAll(" ", "")}\n정보제공처 ${notice.provider}`}</AppText>
          </View>
          {rows.length > 0 && (
            <View
              style={[styles.card, { backgroundColor: COLORS.soft, gap: 12 }]}
            >
              <AppText size={EASY.heading} variant="bold">
                핵심 내용
              </AppText>
              {rows.map((row) => (
                <View key={row.label} style={{ gap: 4 }}>
                  <AppText
                    size={EASY.body}
                    variant="bold"
                    style={{ color: COLORS.primary }}
                  >
                    {row.label}
                  </AppText>
                  <AppText size={EASY.body}>{row.value}</AppText>
                </View>
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
              onPress={() => {
                setTerm(null);
                setEasy((value) => !value);
              }}
            />
            <AppText size={EASY.body} secondary>
              {easy
                ? "점선 표현을 누르면 원문 단어를 볼 수 있어요."
                : "점선 단어를 누르면 뜻을 볼 수 있어요."}
            </AppText>
            <NoticeDocumentText
              comfortable
              notice={notice}
              easy={easy}
              onTermPress={setTerm}
            />
          </View>
          {easy && (!notice.hasEasyText || !notice.easy) && (
            <AppText>쉬운말 결과가 없어 원문을 표시합니다.</AppText>
          )}
          {easy && notice.hasEasyText && !notice.attachmentContentIncluded && (
            <AppText>첨부 내용은 쉬운말 변환에 포함되지 않았습니다.</AppText>
          )}
          <NoticeLinks notice={notice} />
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
