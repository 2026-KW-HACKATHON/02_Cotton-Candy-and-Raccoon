import { Pressable, View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import type { Notice } from "../types/notice";

export function NoticeReadStatus({ notice }: { notice: Notice }) {
  const messages = {
    none: "아직 요약이 없습니다. 원문을 확인해 주세요.",
    pending: "요약을 준비 중입니다. 원문을 먼저 확인할 수 있어요.",
    failed: "요약을 만들지 못했습니다. 원문을 확인해 주세요.",
    needs_review: "원문 확인 필요 · 요약에 확인이 필요한 내용이 있습니다.",
    summarized:
      "AI 요약은 오류가 있을 수 있습니다. 중요한 조건은 원문을 확인해 주세요.",
  };
  return (
    <View style={{ gap: 8 }} accessibilityLiveRegion="polite">
      <AppText>{messages[notice.displayStatus]}</AppText>
      {notice.generatedAt && (
        <AppText secondary>
          요약 기준: {notice.generatedAt.slice(0, 10)}
        </AppText>
      )}
      {notice.omissions.map((message, index) => (
        <AppText key={index}>{message}</AppText>
      ))}
      {!notice.omissions.length && notice.attachmentStatus === "partial" && (
        <AppText>일부 첨부 내용이 요약에 포함되지 않았습니다.</AppText>
      )}
    </View>
  );
}

export function NoticeQueryFeedback({
  error,
  hasData,
  retry,
}: {
  error: unknown;
  hasData: boolean;
  retry: () => void;
}) {
  if (!error) return null;
  return (
    <View style={{ gap: 8 }} accessibilityLiveRegion="polite">
      <AppText>
        {hasData ? "새로 불러오지 못해 이전 내용을 표시합니다. " : ""}
        {error instanceof Error ? error.message : "공문 조회에 실패했습니다."}
      </AppText>
      <Pressable
        accessibilityRole="button"
        onPress={retry}
        style={{ minHeight: 48, justifyContent: "center" }}
      >
        <AppText>다시 불러오기</AppText>
      </Pressable>
    </View>
  );
}
