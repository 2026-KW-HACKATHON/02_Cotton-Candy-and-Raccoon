import { type NoticeFile } from "../types/notice";
export type FilePreviewKind = "image" | "pdf" | "unsupported" | "unknown";
export type FileResponseMetadata = {
  contentType?: string | null;
  contentDisposition?: string | null;
};

function kindFromName(name: string): FilePreviewKind {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name)
    ? "image"
    : /\.pdf$/i.test(name)
      ? "pdf"
      : /\.(hwp|hwpx|docx?|xlsx?|pptx?|zip)$/i.test(name)
        ? "unsupported"
        : "unknown";
}

function responseFileName(disposition: string): string {
  const encoded = disposition.match(
    /(?:^|;)\s*filename\*\s*=\s*UTF-8'[^']*'([^;]+)/i,
  );
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {}
  }
  const plain = disposition.match(
    /(?:^|;)\s*filename\s*=\s*(?:"([^"]*)"|([^;]+))/i,
  );
  return (plain?.[1] ?? plain?.[2] ?? "").trim();
}

export function filePreviewKind(
  file: NoticeFile,
  metadata?: FileResponseMetadata,
): FilePreviewKind {
  const mime = metadata?.contentType?.split(";")[0].trim().toLowerCase();
  if (mime === "application/pdf") return "pdf";
  if (mime && /^image\/(png|jpe?g|gif|webp|bmp|x-ms-bmp)$/.test(mime))
    return "image";
  // 오류 페이지와 명시적인 미지원 MIME은 파일명으로 미리보기 형식을 추측하지 않는다.
  if (
    mime &&
    ![
      "application/octet-stream",
      "binary/octet-stream",
      "application/force-download",
      "application/download",
      "application/x-download",
    ].includes(mime)
  )
    return "unsupported";
  const responseKind = kindFromName(
    responseFileName(metadata?.contentDisposition ?? ""),
  );
  if (responseKind !== "unknown") return responseKind;
  if (file.kind === "inline_image") return "image";
  let name = "";
  try {
    const url = new URL(file.url);
    name = decodeURIComponent(
      url.searchParams.get("filename") ??
        url.searchParams.get("fileName") ??
        url.pathname,
    ).toLowerCase();
  } catch {
    return "unknown";
  }
  return kindFromName(name);
}
export function fileDownloadName(file: NoticeFile) {
  let extension = "";
  try {
    const url = new URL(file.url);
    const name = decodeURIComponent(
      url.searchParams.get("filename") ??
        url.searchParams.get("fileName") ??
        url.pathname,
    );
    extension =
      name
        .match(
          /\.(pdf|png|jpe?g|gif|webp|bmp|hwp|hwpx|docx?|xlsx?|pptx?|zip)$/i,
        )?.[0]
        .toLowerCase() ?? "";
  } catch {}
  return "notice-file-" + file.id + extension;
}
