import { Linking, Platform } from "react-native";
import * as WebBrowser from "expo-web-browser";
import * as FileSystem from "expo-file-system/legacy";
import { httpUrl } from "./noticeContract";
import { fileDownloadName } from "../domain/noticeFiles";
import { type NoticeFile } from "../types/notice";
export async function openNoticeUrl(url: string) {
  const valid = httpUrl(url);
  if (!valid) throw new Error("invalid_url");
  await WebBrowser.openBrowserAsync(valid);
}
export async function downloadNoticeFile(
  file: NoticeFile,
): Promise<"saved" | "cancelled" | "browser"> {
  if (Platform.OS !== "android") {
    await Linking.openURL(file.url);
    return "cancelled";
  }
  if (!httpUrl(file.url) || !FileSystem.cacheDirectory)
    throw new Error("invalid_url");
  const permission =
    await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync();
  if (!permission.granted) return "cancelled";
  const temporary =
    FileSystem.cacheDirectory +
    "notice-" +
    Date.now() +
    "-" +
    fileDownloadName(file);
  let target: string | undefined;
  const download = FileSystem.createDownloadResumable(file.url, temporary);
  const timer = setTimeout(() => {
    void download.cancelAsync().catch(() => undefined);
  }, 30_000);
  try {
    const result = await download.downloadAsync();
    if (!result || result.status < 200 || result.status >= 300)
      throw new Error("download_failed");
    const info = await FileSystem.getInfoAsync(temporary);
    if (!info.exists || info.size === 0) throw new Error("download_failed");
    const header = Object.entries(result.headers).find(
      ([key]) => key.toLowerCase() === "content-type",
    )?.[1];
    const mime = header?.split(";")[0].trim() || "application/octet-stream";
    if (mime === "text/html") throw new Error("download_failed");
    target = await FileSystem.StorageAccessFramework.createFileAsync(
      permission.directoryUri,
      fileDownloadName(file),
      mime,
    );
    // 선택 폴더의 content URI는 copyAsync 대상이 될 수 없어 SAF 쓰기 API를 사용한다.
    const encoded = await FileSystem.readAsStringAsync(temporary, {
      encoding: FileSystem.EncodingType.Base64,
    });
    await FileSystem.StorageAccessFramework.writeAsStringAsync(
      target,
      encoded,
      { encoding: FileSystem.EncodingType.Base64 },
    );
    return "saved";
  } catch (error) {
    if (target)
      await FileSystem.deleteAsync(target, { idempotent: true }).catch(
        () => undefined,
      );
    throw error;
  } finally {
    clearTimeout(timer);
    await FileSystem.deleteAsync(temporary, { idempotent: true }).catch(
      () => undefined,
    );
  }
}
