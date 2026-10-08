import { type ComponentProps } from "react";
import { NoticeState } from "./NoticeState";
export function EasyNoticeState(
  props: Omit<ComponentProps<typeof NoticeState>, "comfortable">,
) {
  return <NoticeState {...props} comfortable />;
}
