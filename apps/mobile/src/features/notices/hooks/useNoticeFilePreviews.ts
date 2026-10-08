import { useQueries } from "@tanstack/react-query";
import { fetchNoticeFilePreviewKind } from "../api/noticeFileMetadata";
import { filePreviewKind } from "../domain/noticeFiles";
import { type NoticeFile } from "../types/notice";

export function useNoticeFilePreviews(
  files: readonly NoticeFile[],
  enabled: boolean,
) {
  const queries = useQueries({
    queries: files.map((file) => ({
      queryKey: ["notice-file-preview", file.id, file.url],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchNoticeFilePreviewKind(file, signal),
      enabled: enabled && filePreviewKind(file) === "unknown",
      staleTime: 10 * 60_000,
      retry: false,
    })),
  });
  return queries.map((query, index) => ({
    kind: query.data ?? filePreviewKind(files[index]),
    checking: query.isFetching,
  }));
}
