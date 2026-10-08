import { httpUrl } from "./noticeContract";
import { filePreviewKind, type FilePreviewKind } from "../domain/noticeFiles";
import { type NoticeFile } from "../types/notice";

export async function fetchNoticeFilePreviewKind(
  file: NoticeFile,
  signal?: AbortSignal,
): Promise<FilePreviewKind> {
  const url = httpUrl(file.url);
  if (!url) throw new Error("invalid_url");
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort);
  if (signal?.aborted) abort();
  const timer = setTimeout(abort, 15_000);
  try {
    // 첨부 목록에서는 파일 본문을 다운로드하지 않고 응답 헤더만 확인한다.
    const response = await fetch(url, {
      method: "HEAD",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("metadata_failed");
    const contentType = response.headers.get("content-type");
    if (contentType?.split(";")[0].trim().toLowerCase() === "text/html")
      throw new Error("metadata_failed");
    return filePreviewKind(file, {
      contentType,
      contentDisposition: response.headers.get("content-disposition"),
    });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
