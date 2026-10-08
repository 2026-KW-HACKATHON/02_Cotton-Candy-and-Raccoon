import { View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS, RADIUS } from "@/shared/theme/tokens";
import { type Notice } from "../types/notice";
import { useNoticeFileActions } from "../hooks/useNoticeFileActions";
import { NoticeActionButton } from "./NoticeActionButton";

export function NoticeOmissions({
  notice,
  comfortable = false,
}: {
  notice: Notice;
  comfortable?: boolean;
}) {
  const actions = useNoticeFileActions();
  const omissions = notice.omissions ?? [];
  if (!omissions.length) return null;
  const size = comfortable ? 20 : 16;
  return (
    <View
      style={{
        gap: 16,
        padding: 20,
        backgroundColor: COLORS.soft,
        borderRadius: RADIUS.card,
      }}
    >
      {omissions.length > 0 && (
        <AppText variant="bold" size={size}>
          요약에 포함되지 않은 첨부
        </AppText>
      )}
      {omissions.map((item, index) => (
        <View key={`omission-${index}`} style={{ gap: 8 }}>
          <AppText size={size}>{item.message}</AppText>
          {item.url && (
            <NoticeActionButton
              comfortable={comfortable}
              disabled={actions.busy}
              label="누락된 첨부 원문 열기"
              onPress={() => {
                void actions.open(item.url!);
              }}
            />
          )}
        </View>
      ))}
      {!!actions.message && (
        <AppText accessibilityLiveRegion="polite" size={size}>
          {actions.message}
        </AppText>
      )}
    </View>
  );
}
