import { useState } from "react";
import { Linking, Pressable, View } from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import type { Notice } from "../types/notice";

export function NoticeLinks({ notice }: { notice: Notice }) {
  const [error, setError] = useState(false);
  function link(label: string, url: string) {
    return (
      <Pressable
        accessibilityRole="link"
        onPress={() => {
          setError(false);
          void Linking.openURL(url).catch(() => setError(true));
        }}
        style={{ minHeight: 48, justifyContent: "center" }}
      >
        <AppText
          style={{ color: COLORS.primary, textDecorationLine: "underline" }}
        >
          {label}
        </AppText>
      </Pressable>
    );
  }
  return (
    <View style={{ gap: 12 }}>
      {notice.url ? (
        link("공식 원문 보기", notice.url)
      ) : (
        <AppText>원문 링크가 없습니다.</AppText>
      )}
      {notice.files.map((file, index) => (
        <View key={file.id}>
          {link(
            `${file.kind === "inline_image" ? "본문 이미지" : "첨부"} ${index + 1}`,
            file.url,
          )}
        </View>
      ))}
      {notice.evidence.length > 0 && (
        <AppText variant="bold">요약의 원문 근거</AppText>
      )}
      {notice.evidence.map((item, index) => (
        <View key={index} style={{ gap: 4 }}>
          <AppText secondary>{item.label}</AppText>
          <AppText>{item.quote}</AppText>
          {item.url && link("근거 파일 보기", item.url)}
        </View>
      ))}
      {error && (
        <AppText accessibilityLiveRegion="polite">
          링크를 열지 못했습니다. 다시 시도해 주세요.
        </AppText>
      )}
    </View>
  );
}
