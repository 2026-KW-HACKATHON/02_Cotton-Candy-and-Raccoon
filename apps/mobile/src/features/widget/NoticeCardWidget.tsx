"use no memo";

// 위젯 라이브러리는 React 렌더러 밖에서 컴포넌트를 호출하므로 컴파일러 메모화를 제외한다.
import {
  FlexWidget,
  OverlapWidget,
  SvgWidget,
  TextWidget,
} from "react-native-android-widget";
import { COLORS } from "@/shared/theme/tokens";
import type { LatestNoticeState } from "./latestNotice";

export function NoticeCardWidget({
  state,
  width,
  height,
}: {
  state: LatestNoticeState;
  width: number;
  height: number;
}) {
  const notice = state.status === "ready" ? state.notice : undefined;
  const message =
    notice?.title ??
    {
      loading: "공문을 가져오고 있어요",
      empty: "등록된 공문이 없습니다",
      error: "공문을 불러오지 못했어요",
      ready: "등록된 공문이 없습니다",
    }[state.status];
  const scale = Math.min(width / 600, height / 146, 1.2);
  const compact = height < 132;
  // 한 줄짜리 홈 격자가 특히 낮으면 제목·제공처·날짜를 우선하고 카테고리 배지는 생략한다.
  const showCategory = height >= 76;
  const boxHeight = Math.min(height, Math.max(146, 146 * scale));
  const cardTop = (height - boxHeight) / 2;
  const cardWidth = width - 94 * scale;
  const cardHeight = boxHeight - (compact ? 0 : 14 * scale);
  const inset = Math.max(16, 32 * scale);
  const verticalInset = compact ? 8 : Math.max(12, 20 * scale);
  const textWidth = cardWidth - inset * 2 - 60 * scale;
  const badgeHeight = compact ? 18 : 24;
  const titleTop =
    verticalInset + (showCategory ? badgeHeight + (compact ? 4 : 8) : 0);
  const footerHeight = compact ? 14 : 18;
  const footerTop = cardHeight - verticalInset - footerHeight;
  const titleHeight = Math.max(16, footerTop - titleTop - (compact ? 4 : 8));
  const titleSize = Math.max(14, 18 * scale);
  const labelSize = Math.max(11, 14 * scale);
  const stripeHeight = cardHeight - 24 * scale;
  // 원형 버튼의 터치 영역을 48dp 확보하고 그 아래에 캐릭터가 들어가도록 축소한다.
  const refreshTouchSize = 48;
  const characterHeight = Math.min(
    117 * scale,
    Math.max(12, cardTop + boxHeight - refreshTouchSize),
  );
  const characterWidth = (characterHeight * 730) / 650;

  return (
    <FlexWidget
      style={{ width, height, alignItems: "center", justifyContent: "center" }}
    >
      <OverlapWidget style={{ width, height }}>
        {/* 최신 공문을 다시 조회하지 않고, 위젯에 실제 표시된 공문의 ID로 상세 화면을 연다. */}
        <OverlapWidget
          clickAction={notice ? "OPEN_URI" : "OPEN_APP"}
          clickActionData={
            notice
              ? { uri: `mobile:///notice/${encodeURIComponent(notice.id)}` }
              : undefined
          }
          accessibilityLabel={`최근 공문 카드. ${notice ? `${state.date} 등록. ` : ""}${message}. ${notice?.provider ?? ""}. ${notice ? "눌러서 공문 상세 보기" : "눌러서 앱 열기"}`}
          style={{ width, height: boxHeight, marginTop: cardTop }}
        >
          <FlexWidget
            style={{
              width: cardWidth,
              height: cardHeight,
              backgroundColor: COLORS.surface,
              borderWidth: 1,
              borderColor: COLORS.border,
              borderRadius: 20 * scale,
            }}
          />
          {[0, 1].map((side) => (
            <FlexWidget
              key={side}
              style={{
                marginLeft: side ? cardWidth - 14 * scale : 8 * scale,
                marginTop: 12 * scale,
                width: 6 * scale,
                height: stripeHeight,
                overflow: "hidden",
              }}
            >
              {Array.from({ length: 12 }, (_, index) => (
                <SvgWidget
                  key={index}
                  svg={
                    index % 2
                      ? require("@/assets/figma/list-imgLeftStripe2.svg")
                      : require("@/assets/figma/list-imgLeftStripe1.svg")
                  }
                  style={{
                    width: 6 * scale,
                    height: 16 * scale,
                    marginBottom: 3 * scale,
                  }}
                />
              ))}
            </FlexWidget>
          ))}
          {showCategory && (
            <TextWidget
              text={notice?.category ?? "최근 공문"}
              maxLines={1}
              style={{
                marginLeft: inset,
                marginTop: verticalInset,
                width: Math.max(66, 92 * scale),
                height: badgeHeight,
                fontSize: labelSize,
                color: COLORS.primary,
                backgroundColor: COLORS.soft,
                borderRadius: 8,
                textAlign: "center",
              }}
            />
          )}
          <TextWidget
            text={message}
            maxLines={Math.max(
              1,
              Math.min(3, Math.floor(titleHeight / (titleSize * 1.4))),
            )}
            truncate="END"
            style={{
              marginLeft: inset,
              marginTop: titleTop,
              width: textWidth,
              height: titleHeight,
              fontSize: titleSize,
              fontWeight: "bold",
              color: COLORS.text,
            }}
          />
          <SvgWidget
            svg={require("@/assets/figma/list-imgPostageStampPerforatedBorder.svg")}
            style={{
              marginLeft: cardWidth - inset - 40 * scale,
              marginTop: verticalInset,
              width: 40 * scale,
              height: 48 * scale,
            }}
          />
          {/* 하단 메타데이터는 제목 영역과 분리하여 긴 제목에서도 제공처가 겹치지 않게 한다. */}
          <TextWidget
            text={
              notice?.provider ??
              (state.status === "error" ? "새로고침으로 재시도" : "월계알리미")
            }
            maxLines={1}
            truncate="END"
            style={{
              marginLeft: inset,
              marginTop: footerTop,
              width: Math.max(1, cardWidth - inset * 2 - 92),
              height: footerHeight,
              fontSize: labelSize,
              color: COLORS.secondary,
            }}
          />
          <TextWidget
            text={notice ? state.date.replaceAll("-", ". ") : ""}
            maxLines={1}
            style={{
              marginLeft: cardWidth - inset - 88,
              marginTop: footerTop,
              width: 88,
              height: footerHeight,
              textAlign: "right",
              fontSize: labelSize,
              color: COLORS.secondary,
            }}
          />
          <SvgWidget
            svg={require("@/assets/images/character-source.svg")}
            style={{
              marginLeft: width - characterWidth,
              marginTop: boxHeight - characterHeight,
              width: characterWidth,
              height: characterHeight,
            }}
          />
        </OverlapWidget>
        {/* 카드 중앙 정렬이나 캐릭터 크기와 무관하게 위젯 전체의 우측 상단에 고정한다. */}
        <FlexWidget
          clickAction="REFRESH"
          accessibilityLabel="최근 공문 새로고침"
          style={{
            marginLeft: width - refreshTouchSize,
            marginTop: 0,
            width: refreshTouchSize,
            height: refreshTouchSize,
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <FlexWidget
            style={{
              width: 32,
              height: 32,
              borderRadius: 16,
              backgroundColor: COLORS.surface,
              borderColor: COLORS.border,
              borderWidth: 1,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <SvgWidget
              svg={require("@/assets/widget/refresh.svg")}
              style={{ width: 18, height: 18 }}
            />
          </FlexWidget>
        </FlexWidget>
      </OverlapWidget>
    </FlexWidget>
  );
}
