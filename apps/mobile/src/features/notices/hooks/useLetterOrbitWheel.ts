import { useCallback, useEffect, useRef } from "react";
import type { SharedValue } from "react-native-reanimated";
import type { View } from "react-native";

type WheelInput = {
  deltaX: number;
  deltaY: number;
  deltaMode: number;
  preventDefault: () => void;
};

export function useLetterOrbitWheel({
  width,
  enabled,
  busy,
  dragInput,
  beginDrag,
  endDrag,
}: {
  width: number;
  enabled: boolean;
  busy: boolean;
  dragInput: SharedValue<number>;
  beginDrag: () => number | null;
  endDrag: (
    translation: number,
    velocity: number,
    cancelled?: boolean,
    expectedToken?: number,
  ) => void;
}) {
  const accumulated = useRef(0);
  const wheelRef = useRef<View>(null);
  const active = useRef(false);
  const token = useRef<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = null;
      active.current = false;
    },
    [width, enabled],
  );

  const onWheel = useCallback(
    (event: WheelInput) => {
      // 세로 휠은 홈 화면 스크롤에 전달하고, 가로 트랙패드/휠만 원형 띠에 연결한다.
      if (!enabled || Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
      if (!active.current) {
        if (busy) return;
        dragInput.set(0);
        token.current = beginDrag();
        if (token.current === null) return;
        active.current = true;
        accumulated.current = 0;
      }
      event.preventDefault();
      const unit =
        event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? width : 1;
      accumulated.current -= event.deltaX * unit;
      dragInput.set(accumulated.current);
      if (timer.current !== null) clearTimeout(timer.current);
      // 휠은 손을 놓는 이벤트가 없어 입력이 잠잠해지면 스냅을 요청한다.
      timer.current = setTimeout(() => {
        active.current = false;
        timer.current = null;
        if (token.current !== null)
          endDrag(accumulated.current, 0, false, token.current);
      }, 140);
    },
    [beginDrag, busy, dragInput, enabled, endDrag, width],
  );

  useEffect(() => {
    if (!enabled) return;
    // React의 passive wheel 리스너에서는 preventDefault가 무시될 수 있어 웹 DOM에만 연결한다.
    type WheelTarget = {
      addEventListener: (
        name: "wheel",
        listener: (event: WheelInput) => void,
        options: { passive: false },
      ) => void;
      removeEventListener: (
        name: "wheel",
        listener: (event: WheelInput) => void,
      ) => void;
    };
    const element = wheelRef.current as unknown as Partial<WheelTarget> | null;
    if (!element?.addEventListener || !element.removeEventListener) return;
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      element.removeEventListener?.("wheel", onWheel);
    };
  }, [enabled, onWheel]);

  return wheelRef;
}
