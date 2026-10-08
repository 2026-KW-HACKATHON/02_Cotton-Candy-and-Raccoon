import { StyleSheet, View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { EasyButton } from "@/shared/ui/EasyButton";
import { COLORS, EASY } from "@/shared/theme/tokens";
import { type Notice } from "../types/notice";

export function EasyNoticeCard({
  notice,
  onOpen,
}: {
  notice: Notice;
  onOpen: () => void;
}) {
  return (
    <View style={styles.card}>
      <AppText size={22} variant="display">
        {notice.title}
      </AppText>
      <AppText size={EASY.body}>{notice.description}</AppText>
      <AppText
        size={EASY.body}
        secondary
      >{`게시일 ${notice.publishedAt.replaceAll(" ", "")}\n정보제공처 ${notice.provider}`}</AppText>
      <EasyButton label="공문 보기" filled onPress={onOpen} />
    </View>
  );
}
const styles = StyleSheet.create({
  card: {
    padding: 20,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: EASY.cardRadius,
    backgroundColor: COLORS.surface,
    gap: 12,
  },
});
