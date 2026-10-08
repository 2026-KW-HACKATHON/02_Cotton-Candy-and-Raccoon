import { useEffect, useState } from "react";
import { AccessibilityInfo, AppState } from "react-native";
import {
  cancelAnimation,
  Easing,
  ReduceMotion,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";

// 모든 부위를 하나의 시계로 구동해 눈·얼굴·꼬리의 타이밍이 어긋나지 않게 한다.
export function useMotionTimeline(durationMs: number, active = true) {
  const progress = useSharedValue(0);
  const [foreground, setForeground] = useState(
    AppState.currentState !== "background" &&
      AppState.currentState !== "inactive",
  );
  const [reduceMotion, setReduceMotion] = useState<boolean | null>(null);
  useEffect(() => {
    let mounted = true;
    void AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => {
        if (mounted) setReduceMotion(value);
      })
      .catch(() => {
        if (mounted) setReduceMotion(false);
      });
    const motionSubscription = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduceMotion,
    );
    const stateSubscription = AppState.addEventListener("change", (state) =>
      setForeground(state === "active"),
    );
    return () => {
      mounted = false;
      motionSubscription.remove();
      stateSubscription.remove();
    };
  }, []);
  useEffect(() => {
    cancelAnimation(progress);
    // 동작 줄이기에서는 등장 모션의 투명한 첫 프레임 대신 완성된 그림을 보여준다.
    progress.value = reduceMotion === false ? 0 : 0.5;
    if (active && foreground && reduceMotion === false) {
      progress.value = withRepeat(
        withTiming(1, {
          duration: durationMs,
          easing: Easing.linear,
          reduceMotion: ReduceMotion.Never,
        }),
        -1,
        false,
        undefined,
        // 접근성 변경은 위 구독에서 처리하므로 반복도 동일한 설정으로 재생한다.
        ReduceMotion.Never,
      );
    }
    return () => cancelAnimation(progress);
  }, [active, durationMs, foreground, progress, reduceMotion]);
  return progress;
}
