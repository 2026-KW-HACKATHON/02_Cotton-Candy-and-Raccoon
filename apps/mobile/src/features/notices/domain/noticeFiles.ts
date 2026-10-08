import { type NoticeFile } from "../types/notice";
export function filePreviewKind(
  file: NoticeFile,
): "image" | "pdf" | "unsupported" {
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
    return "unsupported";
  }
  return /\.(png|jpe?g|gif|webp|bmp)$/.test(name)
    ? "image"
    : /\.pdf$/.test(name)
      ? "pdf"
      : "unsupported";
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
