import { View } from "react-native";
import { Image, type ImageSource } from "expo-image";
import Animated, {
  Easing,
  useAnimatedStyle,
  type SharedValue,
} from "react-native-reanimated";
import type {
  MotionKeyframe,
  MotionLayer,
  MotionScene,
  MotionTracks,
} from "./motionTypes";
import { DETAIL_ASSETS, DETAIL_SCENE } from "./detailScene";
import { SPLASH_ASSETS } from "./splashScene";
import { useMotionTimeline } from "./useMotionTimeline";

const EASE_IN_OUT = Easing.bezier(0.42, 0, 0.58, 1).factory();
const EASE_OUT = Easing.bezier(0, 0, 0.58, 1).factory();

// 원본 Figma 자료는 보존하고, 상세 화면의 꼬리만 정적인 레이어로 표시한다.
const DETAIL_CHARACTER_SCENE: MotionScene = {
  ...DETAIL_SCENE,
  layers: DETAIL_SCENE.layers.map((layer) =>
    layer.id === "120:829" ? { ...layer, tracks: undefined } : layer,
  ),
};

export function sampleMotion(
  track: MotionKeyframe[] | undefined,
  progress: number,
  fallback: number,
) {
  "worklet";
  if (!track?.length) return fallback;
  if (progress <= track[0].at) return track[0].value;
  for (let i = 1; i < track.length; i++) {
    if (progress <= track[i].at) {
      const previous = track[i - 1];
      const fraction = (progress - previous.at) / (track[i].at - previous.at);
      const eased =
        previous.easing === "ease-in-out"
          ? EASE_IN_OUT(fraction)
          : previous.easing === "ease-out"
            ? EASE_OUT(fraction)
            : fraction;
      return previous.value + (track[i].value - previous.value) * eased;
    }
  }
  return track[track.length - 1].value;
}

export function MotionGroup({
  tracks,
  progress,
  style,
  children,
}: {
  tracks?: MotionTracks;
  progress: SharedValue<number>;
  style?: import("react-native").StyleProp<import("react-native").ViewStyle>;
  children: React.ReactNode;
}) {
  const animated = useAnimatedStyle(() => ({
    opacity: sampleMotion(tracks?.opacity, progress.value, 1),
    transform: [
      { translateX: sampleMotion(tracks?.x, progress.value, 0) },
      { translateY: sampleMotion(tracks?.y, progress.value, 0) },
      { rotate: `${sampleMotion(tracks?.rotate, progress.value, 0)}rad` },
      { scaleX: sampleMotion(tracks?.scaleX, progress.value, 1) },
      { scaleY: sampleMotion(tracks?.scaleY, progress.value, 1) },
    ],
  }));
  return <Animated.View style={[style, animated]}>{children}</Animated.View>;
}

function Layer({
  layer,
  assets,
  progress,
}: {
  layer: MotionLayer;
  assets: Record<string, ImageSource>;
  progress: SharedValue<number>;
}) {
  const geometry = {
    position: "absolute" as const,
    left: layer.x,
    top: layer.y,
    width: layer.width,
    height: layer.height,
  };
  if (layer.asset)
    return (
      <Image
        source={assets[layer.asset]}
        contentFit="fill"
        style={[
          geometry,
          layer.rotation
            ? { transform: [{ rotate: `${layer.rotation}deg` }] }
            : undefined,
        ]}
      />
    );
  const children = layer.children?.map((child) => (
    <Layer key={child.id} layer={child} assets={assets} progress={progress} />
  ));
  return layer.tracks ? (
    <MotionGroup tracks={layer.tracks} progress={progress} style={geometry}>
      {children}
    </MotionGroup>
  ) : (
    <View style={geometry}>{children}</View>
  );
}

export function CharacterScene({
  scene,
  assets,
  width,
  progress,
}: {
  scene: MotionScene;
  assets: Record<string, ImageSource>;
  width: number;
  progress: SharedValue<number>;
}) {
  const scale = width / scene.width;
  return (
    <View
      pointerEvents="none"
      accessible={false}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      style={{ width, height: scene.height * scale }}
    >
      <View
        style={{
          width: scene.width,
          height: scene.height,
          transform: [{ scale }],
          transformOrigin: "top left",
        }}
      >
        {scene.layers.map((layer) => (
          <Layer
            key={layer.id}
            layer={layer}
            assets={assets}
            progress={progress}
          />
        ))}
      </View>
    </View>
  );
}

export function DetailCharacter({ active }: { active: boolean }) {
  const progress = useMotionTimeline(DETAIL_SCENE.durationMs, active);
  return (
    <CharacterScene
      scene={DETAIL_CHARACTER_SCENE}
      assets={DETAIL_ASSETS}
      width={131}
      progress={progress}
    />
  );
}

export const SPLASH_IMAGE_SOURCES = [
  ...Object.values(SPLASH_ASSETS),
  require("@/assets/figma/motion/splash-imgSkyRadialGradient.svg"),
  require("@/assets/figma/motion/splash-imgBrandAccentAlignment.svg"),
  require("@/assets/figma/motion/splash-imgLoadingDot1.svg"),
  require("@/assets/figma/motion/splash-imgLoadingDot2.svg"),
  require("@/assets/figma/motion/splash-imgLoadingDot3.svg"),
];
