import { useEffect, useRef, useState } from "react";
import { downloadNoticeFile, openNoticeUrl } from "../api/noticeFileActions";
import { type NoticeFile } from "../types/notice";
export function useNoticeFileActions() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const active = useRef(true);
  const lock = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  async function run(action: () => Promise<string>, fallback: string) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setMessage("");
    try {
      const value = await action();
      if (active.current) setMessage(value);
    } catch {
      if (active.current) setMessage(fallback);
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  return {
    busy,
    message,
    open: (url: string) =>
      run(async () => {
        await openNoticeUrl(url);
        return "";
      }, "파일을 열지 못했어요. 잠시 후 다시 시도해 주세요."),
    download: (file: NoticeFile) =>
      run(async () => {
        const result = await downloadNoticeFile(file);
        return result === "saved"
          ? "다운로드 요청을 완료했어요."
          : result === "browser"
            ? "이 사이트는 앱에서 바로 다운로드할 수 없어요. ‘파일 직접 열기’에서 저장해 주세요."
            : "";
      }, "파일을 내려받지 못했어요. 다시 시도하거나 공식 원문에서 확인해 주세요."),
    clear: () => setMessage(""),
  };
}
