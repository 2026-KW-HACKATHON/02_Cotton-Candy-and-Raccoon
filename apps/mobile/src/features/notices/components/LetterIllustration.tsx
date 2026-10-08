import {
  Pressable,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import { Image } from "expo-image";
import Animated, {
  useAnimatedStyle,
  type SharedValue,
  type AnimatedStyle,
} from "react-native-reanimated";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import { type Notice } from "../types/notice";
import { LETTER_ASSETS, LETTER_LAYERS } from "./letterArtwork";
import { getLetterMotion, type LetterRole } from "./letterInteraction";

export const LETTER_WIDTH = 290;
export const LETTER_HEIGHT = 512;

export function ClosedEnvelope({
  flapStyle,
}: {
  flapStyle?: StyleProp<AnimatedStyle<ViewStyle>>;
}) {
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
      <Animated.View style={[styles.envelopeFlap, flapStyle]}>
        <Image
          source={LETTER_ASSETS.img2}
          contentFit="fill"
          style={StyleSheet.absoluteFill}
        />
      </Animated.View>
    </View>
  );
}

/** 그림 내부 좌표는 유지하고, 편지 문구와 상세 이동은 실제 앱 데이터에 연결한다. */
export function LetterIllustration({
  notice,
  onOpen,
  clock,
  role = "reading",
  interactive = true,
}: {
  notice: Notice;
  onOpen: () => void;
  clock: SharedValue<number>;
  role?: LetterRole;
  interactive?: boolean;
}) {
  const paperStyle = useAnimatedStyle(() => {
    const pose = getLetterMotion(clock.value, role);
    return { transform: [{ translateY: pose.paperY }] };
  });
  const pocketStyle = useAnimatedStyle(() => ({
    zIndex: getLetterMotion(clock.value, role).paperAbove ? 2 : 0,
  }));
  const openStyle = useAnimatedStyle(() => ({
    opacity: getLetterMotion(clock.value, role).open,
  }));
  const closedStyle = useAnimatedStyle(() => ({
    opacity: 1 - getLetterMotion(clock.value, role).open,
  }));
  const flapStyle = useAnimatedStyle(() => {
    const pose = getLetterMotion(clock.value, role);
    return {
      opacity: pose.open === 1 ? 0 : 1,
      transform: [{ scaleY: pose.flap }],
      transformOrigin: "top center" as const,
    };
  });
  const layers = LETTER_LAYERS.filter(
    (layer) => !layer.asset.startsWith("imgEnvelope"),
  );
  return (
    <View style={styles.art}>
      <Animated.View
        pointerEvents="none"
        style={[StyleSheet.absoluteFill, openStyle]}
      >
        <Image
          source={LETTER_ASSETS.imgEnvelopeBack}
          contentFit="fill"
          style={styles.openBack}
        />
      </Animated.View>
      <Animated.View
        style={[styles.pocket, pocketStyle]}
        pointerEvents={interactive ? "auto" : "none"}
        accessibilityElementsHidden={!interactive}
        importantForAccessibility={interactive ? "auto" : "no-hide-descendants"}
      >
        <Animated.View style={[styles.moving, paperStyle]}>
          <View
            pointerEvents="none"
            accessible={false}
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
            style={StyleSheet.absoluteFill}
          >
            {layers.map((layer) => (
              <Image
                key={layer.asset}
                source={
                  LETTER_ASSETS[layer.asset as keyof typeof LETTER_ASSETS]
                }
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
              disabled={!interactive}
              accessibilityState={{ disabled: !interactive }}
              style={({ pressed }) => [
                styles.open,
                pressed && { opacity: 0.75 },
              ]}
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
        </Animated.View>
      </Animated.View>
      <Animated.View
        pointerEvents="none"
        style={[styles.frontLayer, openStyle]}
      >
        <Image
          source={LETTER_ASSETS.imgEnvelopeFront}
          contentFit="fill"
          style={styles.openFront}
        />
      </Animated.View>
      <Animated.View
        pointerEvents="none"
        style={[styles.closedLayer, closedStyle]}
      >
        <ClosedEnvelope flapStyle={flapStyle} />
      </Animated.View>
    </View>
  );
}
const styles = StyleSheet.create({
  art: { width: LETTER_WIDTH, height: LETTER_HEIGHT },
  // 아래로 삽입한 그림만 잘라내고, 들어 올림의 상단 여유 공간은 유지한다.
  pocket: {
    position: "absolute",
    top: -40,
    left: 0,
    width: LETTER_WIDTH,
    height: 544,
    overflow: "hidden",
  },
  moving: {
    position: "absolute",
    top: 40,
    left: 0,
    width: LETTER_WIDTH,
    height: LETTER_HEIGHT,
  },
  frontLayer: { ...StyleSheet.absoluteFill, zIndex: 1 },
  openBack: {
    position: "absolute",
    left: 6.202,
    top: 257.31557,
    width: 277.596,
    height: 246.48386,
  },
  openFront: {
    position: "absolute",
    left: 6.202,
    top: 352.41594,
    width: 277.596,
    height: 151.42012,
  },
  closedLayer: { position: "absolute", left: 0.24, top: 340.5, zIndex: 3 },
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
