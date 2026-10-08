import { useCallback, useEffect, useRef, useState } from "react";
import { AccessibilityInfo, AppState } from "react-native";
import { useFocusEffect } from "expo-router";
import {
  cancelAnimation,
  Easing,
  ReduceMotion,
  useSharedValue,
  withTiming,
} from "react-native-reanimated";
import { scheduleOnRN } from "react-native-worklets";
import { wrapNoticeIndex } from "../domain/letterOrbit";
import { LETTER_STEPS } from "../components/letterInteraction";
import type { Notice } from "../types/notice";

export function useLetterOrbit(notices: Notice[], width: number) {
  const [center, setCenter] = useState(0);
  const [busy, setBusy] = useState(false);
  const [transition, setTransition] = useState<{
    from: number;
    to: number;
  } | null>(null);
  const [reduceMotion, setReduceMotion] = useState(true);
  const clock = useSharedValue(0);
  const position = useSharedValue(0);
  const selectedId = useRef<string | undefined>(notices[0]?.id);
  const generation = useRef(0);
  const locked = useRef(false);
  const focused = useRef(true);
  const foreground = useRef(
    AppState.currentState !== "background" &&
      AppState.currentState !== "inactive",
  );
  const committed = useRef(0);
  const currentNotices = useRef(notices);

  const reset = useCallback(() => {
    generation.current += 1;
    cancelAnimation(clock);
    cancelAnimation(position);
    const data = currentNotices.current;
    const index = data.findIndex((notice) => notice.id === selectedId.current);
    const next = Math.max(0, index);
    selectedId.current = data[next]?.id;
    committed.current = next;
    clock.set(0);
    position.set(next);
    locked.current = false;
    setCenter(next);
    setTransition(null);
    setBusy(false);
  }, [clock, position]);

  useEffect(() => {
    currentNotices.current = notices;
    reset();
  }, [notices, width, reduceMotion, reset]);

  useEffect(() => {
    let mounted = true;
    void AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => {
        if (mounted) setReduceMotion(value);
      })
      .catch(() => {
        if (mounted) setReduceMotion(false);
      });
    const motion = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduceMotion,
    );
    const app = AppState.addEventListener("change", (state) => {
      foreground.current = state === "active";
      if (!foreground.current) reset();
    });
    return () => {
      mounted = false;
      generation.current += 1;
      cancelAnimation(clock);
      cancelAnimation(position);
      motion.remove();
      app.remove();
    };
  }, [clock, position, reset]);

  useFocusEffect(
    useCallback(() => {
      focused.current = true;
      return () => {
        focused.current = false;
        reset();
      };
    }, [reset]),
  );

  const move = useCallback(
    async (direction: -1 | 1) => {
      const data = currentNotices.current;
      if (
        locked.current ||
        data.length < 2 ||
        !focused.current ||
        !foreground.current
      )
        return;
      const from = committed.current;
      const to = from + direction;
      if (reduceMotion) {
        selectedId.current = data[wrapNoticeIndex(to, data.length)].id;
        committed.current = to;
        position.set(to);
        setCenter(to);
        return;
      }
      locked.current = true;
      const token = ++generation.current;
      setBusy(true);
      setTransition({ from, to });
      clock.set(0);
      for (let index = 0; index < LETTER_STEPS.length; index++) {
        // 완료 콜백으로 다음 단계를 진행하며 JS 타이머로 종료 시점을 추측하지 않는다.
        const finished = await new Promise<boolean>((resolve) => {
          clock.set(
            withTiming(
              index + 1,
              {
                duration: LETTER_STEPS[index].duration,
                easing: Easing.bezier(0.42, 0, 0.58, 1),
                reduceMotion: ReduceMotion.Never,
              },
              (done) => {
                scheduleOnRN(resolve, done === true);
              },
            ),
          );
        });
        if (!finished || generation.current !== token) return;
      }
      selectedId.current = data[wrapNoticeIndex(to, data.length)].id;
      committed.current = to;
      position.set(to);
      setCenter(to);
      setTransition(null);
      setBusy(false);
      locked.current = false;
    },
    [clock, position, reduceMotion],
  );

  return { center, busy, transition, clock, position, move };
}
