"use no memo";

// The widget library invokes components outside the React renderer.
import {
  FlexWidget,
  OverlapWidget,
  SvgWidget,
  TextWidget,
} from "react-native-android-widget";
import { LETTER_ARTWORK } from "./letterArtwork";
import type { TodayNoticeState } from "./todayNotice";

export function TodayNoticeWidget({
  state,
  width,
  height,
}: {
  state: TodayNoticeState;
  width: number;
  height: number;
}) {
  const scale = Math.min(width / 314, height / 473);
  const notice = state.status === "ready" ? state.notice : undefined;
  const message =
    notice?.title ??
    {
      loading: "공문을 가져오고 있어요",
      empty: "오늘 등록된 공문이 없습니다",
      error: "공문을 불러오지 못했어요",
      ready: "오늘 등록된 공문이 없습니다",
    }[state.status];
  return (
    <FlexWidget
      style={{ width, height, alignItems: "center", justifyContent: "center" }}
      accessibilityLabel={`${state.date} 공문. ${message}. ${notice?.provider ?? ""}`}
    >
      <OverlapWidget style={{ width: 314 * scale, height: 473 * scale }}>
        <SvgWidget
          svg={LETTER_ARTWORK}
          style={{ width: 314 * scale, height: 473 * scale }}
        />
        <FlexWidget
          style={{
            marginLeft: 54 * scale,
            marginTop: 186 * scale,
            width: 206 * scale,
            height: 108 * scale,
          }}
        >
          {/* Show an absolute date: Android may defer refresh while the device sleeps. */}
          <TextWidget
            text={`${state.date} 공문`}
            style={{
              color: "#208AEF",
              fontSize: 12 * scale,
              fontWeight: "bold",
            }}
          />
          <TextWidget
            text={message}
            maxLines={3}
            truncate="END"
            style={{
              color: "#263449",
              fontSize: 19 * scale,
              fontWeight: "bold",
              height: 70 * scale,
              width: 206 * scale,
            }}
          />
          <TextWidget
            text={
              notice?.provider ??
              (state.status === "error"
                ? "연결을 확인하고 다시 눌러 주세요"
                : "월계알리미")
            }
            maxLines={1}
            truncate="END"
            style={{ color: "#526176", fontSize: 11 * scale }}
          />
        </FlexWidget>
        <FlexWidget
          clickAction="REFRESH"
          accessibilityLabel="오늘의 공문 새로고침"
          style={{
            marginLeft: 86 * scale,
            marginTop: 369 * scale,
            width: 142 * scale,
            height: 48 * scale,
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "#FFFFFF",
            borderRadius: 24 * scale,
          }}
        >
          <TextWidget
            text="새 공문 확인"
            style={{
              color: "#208AEF",
              fontSize: 13 * scale,
              fontWeight: "bold",
            }}
          />
        </FlexWidget>
      </OverlapWidget>
    </FlexWidget>
  );
}
