import { httpUrl } from "./noticeContract";
import { fileDownloadName } from "../domain/noticeFiles";
import { type NoticeFile } from "../types/notice";
export async function openNoticeUrl(url: string) {
  const valid = httpUrl(url);
  if (!valid) throw new Error("invalid_url");
  const tab = window.open(valid, "_blank", "noopener,noreferrer");
  // noopener는 정상적으로 열린 창도 null을 반환할 수 있으므로 실패로 간주하지 않는다.
  void tab;
}
export async function downloadNoticeFile(
  file: NoticeFile,
): Promise<"saved" | "cancelled" | "browser"> {
  if (!httpUrl(file.url)) throw new Error("invalid_url");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30_000);
  try {
    const response = await fetch(file.url, { signal: controller.signal });
    if (
      !response.ok ||
      response.headers.get("content-type")?.includes("text/html")
    )
      throw new Error("download_failed");
    const blob = await response.blob();
    if (!blob.size) throw new Error("empty_file");
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = fileDownloadName(file);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    return "saved";
  } catch (error) {
    // 비동기 요청 뒤 새 창은 차단될 수 있어 직접 열기 버튼으로 안내한다.
    if (error instanceof TypeError) {
      return "browser";
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
