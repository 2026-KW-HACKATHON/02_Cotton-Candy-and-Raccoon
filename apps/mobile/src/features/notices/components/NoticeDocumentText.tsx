import { StyleSheet, View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { noticeDocumentParts } from "../domain/easyTextParts";
import { type GlossaryTerm, type Notice } from "../types/notice";

export function NoticeDocumentText({
  notice,
  easy,
  onTermPress,
  comfortable = false,
}: {
  notice: Notice;
  easy: boolean;
  onTermPress: (term: GlossaryTerm) => void;
  comfortable?: boolean;
}) {
  const parts = noticeDocumentParts(notice, easy);
  return (
    <View style={[styles.paragraphs, comfortable && { gap: 16 }]}>
      <AppText size={comfortable ? 20 : 16} lineHeight={comfortable ? 30 : 24}>
        {parts.map((part, index) => {
          const term = part.term;
          return term ? (
            <AppText
              key={index}
              accessibilityRole="link"
              accessibilityLabel={`${part.text}, ${easy ? "원문 단어" : "단어 뜻"} 보기`}
              onPress={() => onTermPress(term)}
              size={comfortable ? 20 : 16}
              lineHeight={comfortable ? 30 : 24}
              style={styles.linkText}
            >
              {part.text}
            </AppText>
          ) : (
            part.text
          );
        })}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  paragraphs: { gap: 8 },
  linkText: {
    color: COLORS.primary,
    textDecorationLine: "underline",
    textDecorationStyle: "dotted",
  },
});
