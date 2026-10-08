import { Image } from "expo-image";
import { StyleSheet, View, useWindowDimensions } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import {
  CharacterScene,
  MotionGroup,
} from "@/shared/ui/character/AnimatedCharacter";
import { SPLASH_ASSETS, SPLASH_SCENE } from "@/shared/ui/character/splashScene";
import { useMotionTimeline } from "@/shared/ui/character/useMotionTimeline";

const DOTS = [
  {
    id: "321:2220",
    source: require("@/assets/figma/motion/splash-imgLoadingDot1.svg"),
  },
  {
    id: "321:2229",
    source: require("@/assets/figma/motion/splash-imgLoadingDot2.svg"),
  },
  {
    id: "321:2238",
    source: require("@/assets/figma/motion/splash-imgLoadingDot3.svg"),
  },
];

export function AnimatedSplashScreen({
  onVisible,
  fontsReady,
}: {
  onVisible: () => void;
  fontsReady: boolean;
}) {
  const progress = useMotionTimeline(SPLASH_SCENE.durationMs);
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const characterWidth = Math.min(256, Math.max(1, width - 72));
  return (
    <View
      onLayout={onVisible}
      style={styles.screen}
      accessibilityLabel="월계알리미 시작 중"
      accessibilityRole="progressbar"
      accessibilityState={{ busy: true }}
    >
      <MotionGroup
        tracks={SPLASH_SCENE.extras["326:2335"]}
        progress={progress}
        style={{
          position: "absolute",
          width: 520,
          height: 560,
          top: height * 0.0875,
          left: (width - 520) / 2,
        }}
      >
        <Image
          source={require("@/assets/figma/motion/splash-imgSkyRadialGradient.svg")}
          style={{ width: 520, height: 560 }}
        />
      </MotionGroup>
      <View
        style={[
          styles.hero,
          { paddingTop: insets.top, paddingBottom: insets.bottom },
        ]}
      >
        <View style={styles.character}>
          <CharacterScene
            scene={SPLASH_SCENE}
            assets={SPLASH_ASSETS}
            width={characterWidth}
            progress={progress}
          />
        </View>
        <MotionGroup
          tracks={SPLASH_SCENE.extras["321:2205"]}
          progress={progress}
          style={styles.brand}
        >
          <View style={styles.logo}>
            <AppText
              variant="display"
              size={32}
              lineHeight={44}
              style={!fontsReady && { fontFamily: undefined }}
            >
              월계알리미
            </AppText>
            <Image
              source={require("@/assets/figma/motion/splash-imgBrandAccentAlignment.svg")}
              style={{ width: 8, height: 44 }}
            />
          </View>
          <AppText
            secondary
            size={16}
            lineHeight={26}
            style={[styles.subtitle, !fontsReady && { fontFamily: undefined }]}
          >
            우리 동네 소식을 한눈에
          </AppText>
        </MotionGroup>
      </View>
      <View
        style={[
          styles.loading,
          { bottom: Math.max(insets.bottom + 24, height * 0.07) },
        ]}
      >
        <View style={styles.dots}>
          {DOTS.map((dot) => (
            <MotionGroup
              key={dot.id}
              tracks={SPLASH_SCENE.extras[dot.id]}
              progress={progress}
            >
              <Image source={dot.source} style={{ width: 8, height: 8 }} />
            </MotionGroup>
          ))}
        </View>
        <AppText
          secondary
          size={12}
          lineHeight={18}
          style={!fontsReady && { fontFamily: undefined }}
        >
          소식을 준비하고 있어요
        </AppText>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: COLORS.surface, overflow: "hidden" },
  hero: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 24,
    marginBottom: 48,
  },
  character: { height: 304, justifyContent: "center" },
  brand: {
    alignItems: "center",
    gap: 4,
    maxWidth: "100%",
    paddingHorizontal: 16,
  },
  logo: { flexDirection: "row", alignItems: "flex-start", gap: 5.714 },
  subtitle: { textAlign: "center" },
  loading: {
    position: "absolute",
    alignSelf: "center",
    alignItems: "center",
    gap: 12,
  },
  dots: { flexDirection: "row", gap: 8 },
});
