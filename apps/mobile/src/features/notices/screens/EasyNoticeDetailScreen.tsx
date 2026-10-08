import { useState } from "react";
import { Modal, ScrollView, StyleSheet, View } from "react-native";
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
  const [documentOpen, setDocumentOpen] = useState(false);
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
      {query.isPending || query.isError ? (
        <EasyNoticeState
          loading={query.isPending}
          error={query.isError}
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
              text={easy ? notice.easy : notice.original}
              terms={notice.terms}
              easy={easy}
              onTermPress={setTerm}
            />
          </View>
          <EasyButton
            label="원문 파일 보기"
            filled
            onPress={() => setDocumentOpen(true)}
          />
          <AppText size={EASY.body} secondary>
            화면 검토용 예시 공문입니다. 실제 공고가 아닙니다.
          </AppText>
          <AppText size={EASY.body} secondary>
            {
              "월계알리미는 노원구청의 공식 서비스가 아닙니다.\n공개된 공지 정보를 모아 쉽게 전달해요."
            }
          </AppText>
          {/* 첨부 파일 연동 전에는 기본 화면과 동일하게 로컬 예시 원문을 제공한다. */}
          <Modal
            visible={documentOpen}
            transparent
            animationType="slide"
            onRequestClose={() => setDocumentOpen(false)}
          >
            <View style={styles.overlay}>
              <View
                accessibilityViewIsModal
                style={[styles.card, styles.fileModal]}
              >
                <AppText variant="bold" size={EASY.heading}>
                  예시 원문
                </AppText>
                <ScrollView contentContainerStyle={{ gap: 16 }}>
                  <AppText variant="bold" size={EASY.body}>
                    {notice.documentTitle}
                  </AppText>
                  <AppText size={EASY.body}>{notice.original}</AppText>
                  <AppText size={EASY.body}>
                    실제 원문 파일은 서버 연동 후 제공됩니다.
                  </AppText>
                </ScrollView>
                <EasyButton
                  label="닫기"
                  filled
                  onPress={() => setDocumentOpen(false)}
                />
              </View>
            </View>
          </Modal>
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
  overlay: {
    flex: 1,
    justifyContent: "center",
    padding: 20,
    backgroundColor: "rgba(36,59,83,0.24)",
  },
  fileModal: {
    gap: EASY.gap,
    maxWidth: 500,
    width: "100%",
    maxHeight: "85%",
    alignSelf: "center",
  },
});
