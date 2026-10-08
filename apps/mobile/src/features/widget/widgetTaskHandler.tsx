import type { WidgetTaskHandlerProps } from "react-native-android-widget";
import { TodayNoticeWidget } from "./TodayNoticeWidget";
import { koreaDate, loadTodayNotice } from "./todayNotice";

const activeRequests = new Map<number, symbol>();

export async function widgetTaskHandler(props: WidgetTaskHandlerProps) {
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
    const state = await loadTodayNotice();
    if (activeRequests.get(widgetId) !== request) return;
    props.renderWidget(
      <TodayNoticeWidget width={width} height={height} state={state} />,
    );
  } finally {
    if (activeRequests.get(widgetId) === request)
      activeRequests.delete(widgetId);
  }
}
