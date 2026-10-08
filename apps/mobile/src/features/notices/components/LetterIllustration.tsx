import { Pressable, StyleSheet, View } from "react-native";
import { Image } from "expo-image";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { type Notice } from "../types/notice";
import { LETTER_ASSETS, LETTER_LAYERS } from "./letterArtwork";

export const LETTER_WIDTH = 290;
export const LETTER_HEIGHT = 512;

// 닫힌 봉투는 편지 양옆의 장식으로 사용한다.
export function ClosedEnvelope() {
  return (
    <View pointerEvents="none" style={styles.closed}>
      <Image
        source={LETTER_ASSETS.img}
        contentFit="fill"
        style={styles.envelopeBody}
      />
      <Image
        source={LETTER_ASSETS.img1}
        contentFit="fill"
        style={styles.envelopeFront}
      />
      <Image
        source={LETTER_ASSETS.img2}
        contentFit="fill"
        style={styles.envelopeFlap}
      />
    </View>
  );
}

/** 그림 내부 좌표는 유지하고, 편지 문구와 상세 이동은 실제 앱 데이터에 연결한다. */
export function LetterIllustration({
  notice,
  onOpen,
}: {
  notice: Notice;
  onOpen: () => void;
}) {
  return (
    <View style={styles.art}>
      <View
        pointerEvents="none"
        accessible={false}
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
        style={StyleSheet.absoluteFill}
      >
        {LETTER_LAYERS.map((layer) => (
          <Image
            key={layer.asset}
            source={LETTER_ASSETS[layer.asset as keyof typeof LETTER_ASSETS]}
            contentFit="fill"
            style={{
              position: "absolute",
              left: layer.x,
              top: layer.y,
              width: layer.width,
              height: layer.height,
              transform:
                "rotation" in layer
                  ? [{ rotate: `${layer.rotation}deg` }]
                  : undefined,
            }}
          />
        ))}
      </View>
      <View style={styles.copy}>
        <View style={styles.chip}>
          <AppText
            variant="bold"
            size={12}
            lineHeight={18}
            style={{ color: COLORS.primary }}
          >
            {notice.category}
          </AppText>
        </View>
        <View style={styles.titleAndDate}>
          <AppText
            variant="display"
            size={18}
            lineHeight={24}
            numberOfLines={2}
          >
            {notice.title}
          </AppText>
          <AppText secondary size={12} lineHeight={18} numberOfLines={1}>
            {notice.publishedAt} · {notice.provider}
          </AppText>
        </View>
        <AppText size={12} lineHeight={18} numberOfLines={2}>
          {notice.description || "자세한 내용은 원문을 확인해 주세요."}
        </AppText>
        <View style={styles.facts}>
          {[
            {
              label: "기한",
              value: notice.deadline,
              source: LETTER_ASSETS.imgCalendarIcon,
            },
            {
              label: "대상",
              value: notice.audience,
              source: LETTER_ASSETS.imgPersonIcon,
            },
          ].map(({ label, value, source }) => (
            <View key={label} style={styles.fact}>
              <View style={styles.factLabel}>
                <Image source={source} style={styles.factIcon} />
                <AppText
                  variant="bold"
                  size={12}
                  lineHeight={18}
                  style={{ color: COLORS.primary }}
                >
                  {label}
                </AppText>
              </View>
              <AppText
                size={12}
                lineHeight={18}
                numberOfLines={1}
                style={{ flex: 1 }}
              >
                {value || "원문에서 확인"}
              </AppText>
            </View>
          ))}
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${notice.title}, 공문 보기`}
          onPress={onOpen}
          style={({ pressed }) => [styles.open, pressed && { opacity: 0.75 }]}
        >
          <AppText
            variant="medium"
            size={16}
            lineHeight={24}
            style={{ color: COLORS.surface }}
          >
            공문 보기
          </AppText>
        </Pressable>
      </View>
    </View>
  );
}
const styles = StyleSheet.create({
  art: { width: LETTER_WIDTH, height: LETTER_HEIGHT },
  closed: { width: 289.52, height: 170.306 },
  envelopeBody: {
    position: "absolute",
    left: 6.202,
    top: 7.028,
    width: 277.172,
    height: 156.255,
  },
  envelopeFront: {
    position: "absolute",
    left: 8.21,
    top: 78.127,
    width: 272.915,
    height: 82.5983,
  },
  envelopeFlap: {
    position: "absolute",
    left: 8.67,
    top: 7.028,
    width: 272.064,
    height: 93.6681,
  },
  copy: {
    position: "absolute",
    left: 43,
    top: 173.96,
    bottom: 76,
    width: 204,
    gap: 12,
  },
  chip: {
    backgroundColor: COLORS.soft,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
    alignSelf: "flex-start",
  },
  titleAndDate: { gap: 4 },
  facts: { gap: 4 },
  fact: { flexDirection: "row", alignItems: "center", gap: 8 },
  factLabel: { flexDirection: "row", alignItems: "center", gap: 3.412 },
  factIcon: { width: 13.647, height: 13.647 },
  open: {
    marginTop: "auto",
    flexShrink: 0,
    backgroundColor: COLORS.primary,
    borderRadius: 10.235,
    minHeight: 40,
    alignItems: "center",
    justifyContent: "center",
  },
});
