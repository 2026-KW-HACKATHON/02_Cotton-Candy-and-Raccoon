import { Pressable, StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { splitGlossaryText } from "../domain/noticePresentation";
import { type GlossaryTerm } from "../types/notice";

export function NoticeDocumentText({
  text,
  terms,
  easy,
  onTermPress,
}: {
  text: string;
  terms?: readonly GlossaryTerm[];
  easy: boolean;
  onTermPress: (term: GlossaryTerm) => void;
}) {
  return (
    <View style={styles.paragraphs}>
      {text.split("\n").map((paragraph, index) => (
        <View key={index} style={styles.paragraph}>
          {splitGlossaryText(
            paragraph,
            terms,
            easy ? "plain" : "original",
          ).flatMap((part, partIndex) =>
            part.term
              ? [
                  <Pressable
                    key={`${partIndex}-term`}
                    accessibilityRole="button"
                    accessibilityLabel={`${part.text}, ${easy ? "원문 단어" : "단어 뜻"} 보기`}
                    onPress={() => onTermPress(part.term!)}
                    hitSlop={{ top: 10, bottom: 10 }}
                    style={styles.link}
                  >
                    <AppText size={16} lineHeight={24} style={styles.linkText}>
                      {part.text}
                    </AppText>
                    <Image
                      source={require("@/assets/figma/detail-overlay/word-underline.svg")}
                      contentFit="fill"
                      style={styles.underline}
                    />
                  </Pressable>,
                ]
              : part.text
                  .split(/(\s+)/)
                  .filter(Boolean)
                  .map((word, wordIndex) => (
                    <AppText
                      key={`${partIndex}-${wordIndex}`}
                      size={16}
                      lineHeight={24}
                    >
                      {word}
                    </AppText>
                  )),
          )}
        </View>
      ))}
    </View>
  );
}
const styles = StyleSheet.create({
  paragraphs: { gap: 8 },
  paragraph: { flexDirection: "row", flexWrap: "wrap", alignItems: "center" },
  // 링크의 상하 여백이 본문 줄간격을 늘리지 않도록 터치 여유는 hitSlop으로 제공한다.
  link: { minHeight: 24, maxWidth: "100%" },
  linkText: { color: COLORS.primary },
  underline: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    height: 1.5,
  },
});
