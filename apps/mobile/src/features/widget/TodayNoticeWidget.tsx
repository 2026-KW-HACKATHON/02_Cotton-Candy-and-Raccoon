"use no memo";

// The widget library invokes components outside the React renderer.
import {
  FlexWidget,
  OverlapWidget,
  SvgWidget,
  TextWidget,
} from "react-native-android-widget";
import { LETTER_ARTWORK } from "./letterArtwork";
import type { LatestNoticeState } from "./latestNotice";

export function TodayNoticeWidget({
  state,
  width,
  height,
}: {
  state: LatestNoticeState;
  width: number;
  height: number;
}) {
  const scale = Math.min(width / 314, height / 473);
  const notice = state.status === "ready" ? state.notice : undefined;
  const message =
    notice?.title ??
    {
      loading: "공문을 가져오고 있어요",
      empty: "등록된 공문이 없습니다",
      error: "공문을 불러오지 못했어요",
      ready: "등록된 공문이 없습니다",
    }[state.status];
  return (
    <FlexWidget
      style={{ width, height, alignItems: "center", justifyContent: "center" }}
      accessibilityLabel={`최근 공문. ${notice ? `${state.date} 등록. ` : ""}${message}. ${notice?.provider ?? ""}`}
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
            height: 120 * scale,
          }}
        >
          {/* 갱신 시각이 아닌 공문의 실제 등록일을 표시해 이전 공문을 오늘 공문으로 오해하지 않게 한다. */}
          <TextWidget
            text={notice ? `${state.date} 공문` : "최근 공문"}
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
              height: 92 * scale,
              width: 206 * scale,
            }}
          />
        </FlexWidget>
        {/* 제목이 세 줄이어도 제공처와 겹치지 않도록 편지 하단의 별도 영역에 배치한다. */}
        <FlexWidget
          style={{
            marginLeft: 54 * scale,
            marginTop: 310 * scale,
            width: 206 * scale,
            height: 20 * scale,
          }}
        >
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
          accessibilityLabel="최근 공문 새로고침"
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
