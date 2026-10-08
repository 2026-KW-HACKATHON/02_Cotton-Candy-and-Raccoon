import { View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { type Notice } from "../types/notice";
const MESSAGES = {
  none: "아직 요약이 없어요. 원문을 확인해 주세요.",
  pending: "요약을 준비 중이에요. 먼저 원문을 확인할 수 있어요.",
  failed: "요약을 만들지 못했어요. 원문을 확인해 주세요.",
  needs_review:
    "원문 확인이 필요한 공문이에요. 중요한 조건은 공식 원문과 첨부파일을 확인해 주세요.",
};
export function NoticeSummaryStatus({
  status,
  comfortable = false,
}: {
  status: Notice["summaryStatus"];
  comfortable?: boolean;
}) {
  if (!status || status === "summarized") return null;
  return (
    <View
      style={{ backgroundColor: COLORS.soft, borderRadius: 16, padding: 16 }}
    >
      <AppText size={comfortable ? 20 : 16}>{MESSAGES[status]}</AppText>
    </View>
  );
}
