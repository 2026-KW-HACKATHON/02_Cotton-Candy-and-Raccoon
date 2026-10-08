import { useCallback, useEffect, useRef, useState } from "react";
import { AccessibilityInfo, AppState } from "react-native";
import { useFocusEffect } from "expo-router";
import {
  cancelAnimation,
  Easing,
  ReduceMotion,
  useAnimatedReaction,
  useSharedValue,
  withSequence,
  withTiming,
} from "react-native-reanimated";
import { scheduleOnRN } from "react-native-worklets";
import { wrapNoticeIndex } from "../domain/letterOrbit";
import {
  getDragPose,
  getSnapDirection,
  haveSameNoticeOrder,
} from "../domain/letterOrbitInput";
import { LETTER_STEPS } from "../components/letterInteraction";
import type { Notice } from "../types/notice";

export type OrbitTransition = { from: number | null; to: number | null };
const TIMING = {
  easing: Easing.bezier(0.42, 0, 0.58, 1),
  reduceMotion: ReduceMotion.Never,
};

export function useLetterOrbit(notices: Notice[], width: number) {
  const [center, setCenter] = useState(0);
  const [busy, setBusy] = useState(false);
  const [transition, setTransition] = useState<OrbitTransition | null>(null);
  // 전환 중 표시 내용만 고정하고 완료 후 최신 Query 결과로 돌아간다.
  const [snapshot, setSnapshot] = useState<Notice[] | null>(null);
  const [reduceMotion, setReduceMotion] = useState(true);
  const clock = useSharedValue(0);
  const position = useSharedValue(0);
  const dragInput = useSharedValue(0);
  const base = useSharedValue(0);
  const target = useSharedValue(0);
  // 0: 대기, 1: 드래그, 2: 자동 재생, 3: 스냅 또는 개폐 완료 대기.
  const mode = useSharedValue(0);
  const spacing = Math.max(120, 210 * Math.min(1, (width - 40) / 290));
  const mounted = useRef(true);
  const generation = useRef(0);
  const locked = useRef(false);
  const focused = useRef(true);
  const foreground = useRef(
    AppState.currentState !== "background" &&
      AppState.currentState !== "inactive",
  );
  const committed = useRef(0);
  const selectedId = useRef<string | undefined>(notices[0]?.id);
  const currentNotices = useRef(notices);
  const previousOrder = useRef(notices);
  const suppressClick = useRef(false);

  // 드래그 입력만 읽고 개폐·위치를 출력한다. 출력값을 다시 입력으로 구독하지 않는다.
  useAnimatedReaction(
    () => {
      if (mode.value !== 1) return null;
      const pose = getDragPose(dragInput.value, spacing);
      return { stage: pose.stage, position: base.value + pose.offset };
    },
    (pose) => {
      if (pose === null) return;
      clock.set(pose.stage);
      position.set(pose.position);
    },
  );

  // 자동 재생은 개폐 진행값을 입력으로만 사용하고 원호 위치만 갱신한다.
  useAnimatedReaction(
    () => {
      if (mode.value !== 2) return null;
      const progress = Math.max(0, Math.min(1, clock.value - 5));
      return base.value + (target.value - base.value) * progress;
    },
    (nextPosition) => {
      if (nextPosition !== null) position.set(nextPosition);
    },
  );

  const restore = useCallback(
    (next: number) => {
      mode.set(0);
      cancelAnimation(clock);
      cancelAnimation(position);
      committed.current = next;
      selectedId.current =
        currentNotices.current[
          wrapNoticeIndex(next, currentNotices.current.length)
        ]?.id;
      position.set(next);
      // 완료된 편지가 React의 읽기 역할로 바뀌기 전 닫힘 프레임으로 되돌아가지 않게 한다.
      // 진행값은 다음 입력의 start에서 초기화한다.
      locked.current = false;
      if (mounted.current) {
        setCenter(next);
        setTransition(null);
        setSnapshot(null);
        setBusy(false);
      }
    },
    [clock, mode, position],
  );

  const reset = useCallback(() => {
    generation.current += 1;
    const index = currentNotices.current.findIndex(
      (notice) => notice.id === selectedId.current,
    );
    restore(Math.max(0, index));
  }, [restore]);

  // 최신 참조 갱신과 목록 구조 변경을 분리한다. 내용 갱신은 진행 중 모션을 취소하지 않는다.
  useEffect(() => {
    currentNotices.current = notices;
    if (!haveSameNoticeOrder(previousOrder.current, notices)) reset();
    previousOrder.current = notices;
  }, [notices, reset]);
  useEffect(() => {
    reset();
  }, [width, reduceMotion, reset]);

  useEffect(() => {
    mounted.current = true;
    void AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => {
        if (mounted.current) setReduceMotion(value);
      })
      .catch(() => {
        if (mounted.current) setReduceMotion(false);
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
      mounted.current = false;
      generation.current += 1;
      mode.set(0);
      cancelAnimation(clock);
      cancelAnimation(position);
      motion.remove();
      app.remove();
    };
  }, [clock, mode, position, reset]);
  useFocusEffect(
    useCallback(() => {
      focused.current = true;
      return () => {
        focused.current = false;
        reset();
      };
    }, [reset]),
  );

  const finish = useCallback(
    (token: number, next: number, finished: boolean) => {
      // 이전 완료·취소 콜백이 새로운 동작을 해제하지 않도록 토큰을 확인한다.
      if (!mounted.current || token !== generation.current) return;
      generation.current += 1;
      restore(finished ? next : committed.current);
    },
    [restore],
  );

  const start = useCallback(() => {
    if (
      locked.current ||
      currentNotices.current.length < 2 ||
      !focused.current ||
      !foreground.current
    )
      return null;
    const token = ++generation.current;
    locked.current = true;
    base.set(committed.current);
    clock.set(0);
    setSnapshot(currentNotices.current);
    setBusy(true);
    return token;
  }, [base, clock]);

  const play = useCallback(
    (startStep: number, token: number, next: number) => {
      try {
        const animations = LETTER_STEPS.slice(startStep).map(
          (step, offset, steps) =>
            withTiming(
              startStep + offset + 1,
              { ...TIMING, duration: step.duration },
              offset === steps.length - 1
                ? (done) => {
                    "worklet";
                    scheduleOnRN(finish, token, next, done === true);
                  }
                : undefined,
            ),
        );
        // 단계 경계마다 JS로 돌아오지 않고 UI 측에서 연속 재생한다.
        clock.set(withSequence(...animations));
      } catch {
        finish(token, next, false);
      }
    },
    [clock, finish],
  );

  const move = useCallback(
    (direction: -1 | 1) => {
      const token = start();
      if (token === null) return;
      const from = committed.current;
      const next = from + direction;
      if (reduceMotion) {
        finish(token, next, true);
        return;
      }
      setTransition({ from, to: next });
      target.set(next);
      mode.set(2);
      play(0, token, next);
    },
    [finish, mode, play, reduceMotion, start, target],
  );

  const beginDrag = useCallback(() => {
    const token = start();
    if (token === null) return null;
    suppressClick.current = true;
    setTransition({ from: committed.current, to: null });
    mode.set(reduceMotion ? 0 : 1);
    return token;
  }, [mode, reduceMotion, start]);

  const openAfterSnap = useCallback(
    (token: number, next: number, done: boolean) => {
      if (token !== generation.current || !mounted.current) return;
      if (!done) {
        finish(token, next, false);
        return;
      }
      setTransition({ from: null, to: next });
      clock.set(6);
      play(6, token, next);
    },
    [clock, finish, play],
  );

  const endDrag = useCallback(
    (
      translation: number,
      velocity: number,
      cancelled = false,
      expectedToken?: number,
    ) => {
      if (expectedToken !== undefined && expectedToken !== generation.current)
        return;
      if (!locked.current || mode.get() === 2 || mode.get() === 3) return;
      const token = generation.current;
      const from = committed.current;
      const pose = getDragPose(translation, spacing);
      const direction = cancelled
        ? 0
        : getSnapDirection(pose.offset, -velocity / spacing);
      const next = from + direction;
      mode.set(3);
      if (reduceMotion) {
        finish(token, next, true);
        return;
      }
      try {
        // 짧은 드래그는 부분적으로 닫힌 편지를 읽기 상태로 되돌린다.
        if (pose.stage < 5) {
          clock.set(
            withTiming(0, { ...TIMING, duration: 160 }, (done) => {
              scheduleOnRN(finish, token, from, done === true);
            }),
          );
        } else {
          clock.set(5);
          position.set(
            withTiming(next, { ...TIMING, duration: 220 }, (done) => {
              scheduleOnRN(openAfterSnap, token, next, done === true);
            }),
          );
        }
      } catch {
        finish(token, from, false);
      }
    },
    [clock, finish, mode, openAfterSnap, position, reduceMotion, spacing],
  );

  const beginTouch = useCallback(() => {
    suppressClick.current = false;
  }, []);
  const click = useCallback(
    (direction: -1 | 1) => {
      if (!suppressClick.current) move(direction);
    },
    [move],
  );

  return {
    center,
    busy,
    transition,
    clock,
    position,
    dragInput,
    beginDrag,
    endDrag,
    beginTouch,
    click,
    move,
    displayNotices: snapshot ?? notices,
  };
}
