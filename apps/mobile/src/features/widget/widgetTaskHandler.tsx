import type { WidgetTaskHandlerProps } from "react-native-android-widget";
import { TodayNoticeWidget } from "./TodayNoticeWidget";
import { koreaDate, loadLatestNotice } from "./latestNotice";

const activeRequests = new Map<number, symbol>();

export async function widgetTaskHandler(props: WidgetTaskHandlerProps) {
  // 기존에 설치된 위젯을 유지하도록 네이티브 등록 이름은 변경하지 않는다.
  if (props.widgetInfo.widgetName !== "TodayNotice") return;
  const { widgetId, width, height } = props.widgetInfo;
  if (props.widgetAction === "WIDGET_DELETED") {
    activeRequests.delete(widgetId);
    return;
  }
  if (props.widgetAction === "WIDGET_CLICK" && props.clickAction !== "REFRESH")
    return;
  const request = Symbol();
  activeRequests.set(widgetId, request);
  try {
    props.renderWidget(
      <TodayNoticeWidget
        width={width}
        height={height}
        state={{ date: koreaDate(), status: "loading" }}
      />,
    );
    const state = await loadLatestNotice();
    if (activeRequests.get(widgetId) !== request) return;
    props.renderWidget(
      <TodayNoticeWidget width={width} height={height} state={state} />,
    );
  } finally {
    if (activeRequests.get(widgetId) === request)
      activeRequests.delete(widgetId);
  }
}
