import {
  Platform,
  Pressable,
  StyleSheet,
  View,
  useWindowDimensions,
} from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, {
  useAnimatedStyle,
  type SharedValue,
} from "react-native-reanimated";
import { ChevronLeft, ChevronRight } from "lucide-react-native";
import { AppText } from "@/shared/ui/AppText";
import { IconButton } from "@/shared/ui/IconButton";
import { COLORS } from "@/shared/theme/tokens";
import {
  getOrbitPose,
  getOrbitSlots,
  wrapNoticeIndex,
} from "../domain/letterOrbit";
import { useLetterOrbit } from "../hooks/useLetterOrbit";
import {
  LetterIllustration,
  LETTER_HEIGHT,
  LETTER_WIDTH,
} from "./LetterIllustration";
import type { LetterRole } from "./letterInteraction";
import type { Notice } from "../types/notice";

function OrbitSlot({
  slot,
  width,
  scale,
  clock,
  position,
  transition,
  notice,
  role,
  interactive,
  onOpen,
}: {
  slot: number;
  width: number;
  scale: number;
  clock: SharedValue<number>;
  position: SharedValue<number>;
  transition: { from: number; to: number } | null;
  notice: Notice;
  role: LetterRole;
  interactive: boolean;
  onOpen: () => void;
}) {
  const style = useAnimatedStyle(() => {
    const progress = Math.max(0, Math.min(1, clock.value - 5));
    const current = transition
      ? transition.from + (transition.to - transition.from) * progress
      : position.value;
    const pose = getOrbitPose(slot, current, scale);
    return {
      opacity: pose.opacity,
      zIndex: Math.round(100 - Math.abs(slot - current) * 10),
      transform: [
        { translateX: pose.x },
        { translateY: pose.y },
        { rotate: `${pose.rotation}rad` },
        { scale },
      ],
    };
  });
  return (
    <Animated.View
      pointerEvents={interactive ? "auto" : "none"}
      style={[
        styles.slot,
        {
          left: (width - LETTER_WIDTH) / 2,
          top: 12 + LETTER_HEIGHT * scale - LETTER_HEIGHT,
        },
        style,
      ]}
    >
      <LetterIllustration
        notice={notice}
        onOpen={onOpen}
        clock={clock}
        role={role}
        interactive={interactive}
      />
    </Animated.View>
  );
}

export function LetterOrbit({
  notices,
  onOpen,
}: {
  notices: Notice[];
  onOpen: (notice: Notice) => void;
}) {
  const width = Math.min(useWindowDimensions().width, 600);
  const scale = Math.min(1, (width - 40) / LETTER_WIDTH);
  const orbit = useLetterOrbit(notices, width);
  const selectedIndex = wrapNoticeIndex(orbit.center, notices.length);
  const disabled = orbit.busy || notices.length < 2;
  const pan = Gesture.Pan()
    .activeOffsetX([-24, 24])
    .failOffsetY([-16, 16])
    .runOnJS(true)
    .onEnd((event) => {
      if (disabled) return;
      if (
        Math.abs(event.translationX) >= 40 ||
        Math.abs(event.velocityX) >= 450
      ) {
        void orbit.move(
          event.translationX === 0
            ? event.velocityX < 0
              ? 1
              : -1
            : event.translationX < 0
              ? 1
              : -1,
        );
      }
    });
  const keyboardProps =
    Platform.OS === "web"
      ? {
          tabIndex: 0 as const,
          onKeyDown: (event: {
            key: string;
            target: unknown;
            currentTarget: unknown;
            preventDefault: () => void;
          }) => {
            if (event.target !== event.currentTarget || disabled) return;
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              void orbit.move(event.key === "ArrowLeft" ? -1 : 1);
            }
          },
        }
      : {};
  if (!notices.length) return null;
  return (
    <View {...keyboardProps}>
      <GestureDetector gesture={pan}>
        <View
          testID="letter-orbit"
          style={{
            height: 12 + LETTER_HEIGHT * scale + 92,
            overflow: "hidden",
          }}
        >
          {getOrbitSlots(orbit.center).map((slot) => {
            const notice = notices[wrapNoticeIndex(slot, notices.length)];
            const role: LetterRole = orbit.transition
              ? slot === orbit.transition.from
                ? "outgoing"
                : slot === orbit.transition.to
                  ? "incoming"
                  : "closed"
              : slot === orbit.center
                ? "reading"
                : "closed";
            const interactive = !orbit.busy && slot === orbit.center;
            return (
              <OrbitSlot
                key={`${slot}:${notice.id}`}
                slot={slot}
                width={width}
                scale={scale}
                clock={orbit.clock}
                position={orbit.position}
                transition={orbit.transition}
                notice={notice}
                role={role}
                interactive={interactive}
                onOpen={() => onOpen(notice)}
              />
            );
          })}
          {([-1, 1] as const).map((direction) => (
            <Pressable
              key={direction}
              accessible={notices.length > 1}
              accessibilityElementsHidden={notices.length < 2}
              importantForAccessibility={
                notices.length < 2 ? "no-hide-descendants" : "auto"
              }
              accessibilityRole="button"
              accessibilityLabel={
                direction < 0 ? "이전 공문 봉투" : "다음 공문 봉투"
              }
              accessibilityState={{ disabled }}
              disabled={disabled}
              onPress={() => {
                void orbit.move(direction);
              }}
              style={[
                styles.envelopeTouch,
                {
                  top: 350 * scale,
                  height: 190 * scale,
                  width: Math.max(40, (width - LETTER_WIDTH * scale) / 2 + 15),
                  [direction < 0 ? "left" : "right"]: 0,
                },
              ]}
            />
          ))}
        </View>
      </GestureDetector>
      <View style={styles.controls}>
        <IconButton
          accessibilityLabel="이전 공문"
          accessibilityState={{ disabled, busy: orbit.busy }}
          disabled={disabled}
          onPress={() => {
            void orbit.move(-1);
          }}
          style={styles.arrow}
        >
          <ChevronLeft size={20} color={COLORS.primary} />
        </IconButton>
        <AppText
          variant="medium"
          secondary
          size={16}
          lineHeight={24}
          accessibilityLiveRegion="polite"
          style={styles.counter}
        >
          {selectedIndex + 1} / {notices.length}
        </AppText>
        <IconButton
          accessibilityLabel="다음 공문"
          accessibilityState={{ disabled, busy: orbit.busy }}
          disabled={disabled}
          onPress={() => {
            void orbit.move(1);
          }}
          style={styles.arrow}
        >
          <ChevronRight size={20} color={COLORS.primary} />
        </IconButton>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  slot: {
    position: "absolute",
    width: LETTER_WIDTH,
    height: LETTER_HEIGHT,
    transformOrigin: "bottom center",
  },
  envelopeTouch: { position: "absolute", zIndex: 110 },
  controls: {
    marginTop: -74,
    flexDirection: "row",
    gap: 20,
    alignItems: "center",
    justifyContent: "center",
  },
  arrow: { minWidth: 48, minHeight: 48 },
  counter: { width: 64, textAlign: "center" },
});
