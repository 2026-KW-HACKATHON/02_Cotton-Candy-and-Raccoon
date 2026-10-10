import {
  highlightDocumentParts,
  type EvidenceRange,
} from "../domain/summaryEvidence";
import { Pressable, StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { splitGlossaryText } from "../domain/noticePresentation";
import { type DocumentPart, type GlossaryTerm } from "../types/notice";

export function NoticeDocumentText({
  text,
  terms,
  easy,
  onTermPress,
  comfortable = false,
  parts,
  interactive = true,
  highlights = [],
}: {
  text: string;
  terms?: readonly GlossaryTerm[];
  easy: boolean;
  onTermPress: (term: GlossaryTerm) => void;
  comfortable?: boolean;
  parts?: DocumentPart[];
  interactive?: boolean;
  highlights?: readonly EvidenceRange[];
}) {
  const paragraphs: DocumentPart[][] = [[]];
  const sourceParts =
    parts ?? splitGlossaryText(text, terms, easy ? "plain" : "original");
  for (const part of highlightDocumentParts(
    sourceParts,
    easy ? [] : highlights,
  )) {
    part.text.split("\n").forEach((value, index) => {
      if (index > 0) paragraphs.push([]);
      paragraphs[paragraphs.length - 1].push({ ...part, text: value });
    });
  }
  return (
    <View style={[styles.paragraphs, comfortable && { gap: 16 }]}>
      {paragraphs.map((paragraph, index) => (
        <View key={index} style={styles.paragraph}>
          {paragraph.flatMap((part, partIndex) =>
            part.term
              ? [
                  <Pressable
                    key={`${partIndex}-term`}
                    accessibilityRole="button"
                    accessibilityLabel={`${part.text}, ${easy ? "원문 단어" : "단어 뜻"} 보기`}
                    disabled={!interactive}
                    accessibilityState={{ disabled: !interactive }}
                    onPress={
                      interactive ? () => onTermPress(part.term!) : undefined
                    }
                    hitSlop={comfortable ? undefined : { top: 10, bottom: 10 }}
                    style={[styles.link, comfortable && styles.comfortableLink]}
                  >
                    <AppText
                      size={comfortable ? 20 : 16}
                      lineHeight={comfortable ? 30 : 24}
                      style={[
                        styles.linkText,
                        part.highlighted && styles.highlight,
                      ]}
                    >
                      {part.text}
                    </AppText>
                    <Image
                      source={require("@/assets/figma/detail-overlay/word-underline.svg")}
                      contentFit="fill"
                      style={[styles.underline, comfortable && { bottom: 12 }]}
                    />
                  </Pressable>,
                ]
              : part.text
                  .split(/(\s+)/)
                  .filter(Boolean)
                  .map((word, wordIndex) => (
                    <AppText
                      key={`${partIndex}-${wordIndex}`}
                      size={comfortable ? 20 : 16}
                      lineHeight={comfortable ? 30 : 24}
                      style={[
                        comfortable && { maxWidth: "100%" },
                        part.highlighted && styles.highlight,
                      ]}
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
  highlight: { backgroundColor: "#FFF2A8" },
  paragraphs: { gap: 8 },
  paragraph: { flexDirection: "row", flexWrap: "wrap", alignItems: "center" },
  // 링크의 상하 여백이 본문 줄간격을 늘리지 않도록 터치 여유는 hitSlop으로 제공한다.
  link: { minHeight: 24, maxWidth: "100%" },
  comfortableLink: { minHeight: 56, paddingVertical: 12 },
  linkText: { color: COLORS.primary },
  underline: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    height: 1.5,
  },
});
