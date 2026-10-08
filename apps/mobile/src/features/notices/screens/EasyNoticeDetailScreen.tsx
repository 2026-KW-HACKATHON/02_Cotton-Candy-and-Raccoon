import { useState } from "react";
import { DeadlineReminder } from "@/features/notifications/DeadlineReminder";
import { Modal, StyleSheet, View } from "react-native";
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
import { getSummaryRows } from "../domain/noticePresentation";
import { easyTextParts } from "../domain/easyTextParts";
import { type GlossaryTerm } from "../types/notice";

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
      header={
        <View style={styles.actions}>
          <EasyButton
            label="이전 화면으로"
            onPress={goBack}
            style={{ flex: 1 }}
          />
          <EasyButton
            label={saved ? "저장 취소" : "저장하기"}
            subtitle={saved ? "저장됨" : "저장 안 됨"}
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
          <DeadlineReminder key={notice.id} notice={notice} />
          <View style={{ gap: 12 }}>
            <AppText size={EASY.title} variant="display">
              {notice.title}
            </AppText>
            <AppText
              size={EASY.body}
              secondary
            >{`게시일 ${notice.publishedAt.replaceAll(" ", "")}\n정보제공처 ${notice.provider}`}</AppText>
          </View>
          {rows.length > 0 && (
            <View
              style={[styles.card, { backgroundColor: COLORS.soft, gap: 12 }]}
            >
              <AppText size={EASY.heading} variant="display">
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
            <AppText size={EASY.heading} variant="display">
              공문 원문
            </AppText>
            <EasyButton
              label={`쉬운말 보기 · ${easy ? "켜짐" : "꺼짐"}`}
              filled={easy}
              selected={easy}
              onPress={() => setEasy((value) => !value)}
            />
            <AppText size={EASY.body}>
              {easy && notice.hasEasyText && notice.easy
                ? easyTextParts(
                    notice.easyOriginal,
                    notice.easy,
                    notice.easyChanges,
                  ).map((part, index) =>
                    part.term ? (
                      <AppText
                        key={index}
                        size={EASY.body}
                        accessibilityRole="link"
                        accessibilityLabel={`${part.text}, 원래 용어 보기`}
                        onPress={() => setTerm(part.term ?? null)}
                        style={{
                          color: COLORS.primary,
                          textDecorationLine: "underline",
                        }}
                      >
                        {part.text}
                      </AppText>
                    ) : (
                      part.text
                    ),
                  )
                : notice.original ||
                  "본문 텍스트가 없습니다. 원문과 첨부를 확인해 주세요."}
            </AppText>
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
      <Modal
        visible={term !== null}
        transparent
        animationType="fade"
        onRequestClose={() => setTerm(null)}
      >
        <View style={styles.overlay}>
          <View
            accessibilityViewIsModal
            style={[
              styles.card,
              {
                gap: EASY.gap,
                maxWidth: 500,
                width: "100%",
                alignSelf: "center",
              },
            ]}
          >
            <AppText size={EASY.heading} variant="display">
              원래 용어
            </AppText>
            <AppText size={EASY.body}>
              {term?.plain} · {term?.original}
            </AppText>
            <EasyButton label="닫기" filled onPress={() => setTerm(null)} />
          </View>
        </View>
      </Modal>
    </Screen>
  );
}
const styles = StyleSheet.create({
  actions: {
    flexDirection: "row",
    paddingHorizontal: 8,
    paddingTop: 12,
    paddingBottom: 13,
    gap: 8,
  },
  card: {
    padding: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: EASY.cardRadius,
    backgroundColor: COLORS.surface,
  },
  overlay: {
    flex: 1,
    justifyContent: "center",
    padding: 20,
    backgroundColor: "rgba(36,59,83,0.4)",
  },
});
