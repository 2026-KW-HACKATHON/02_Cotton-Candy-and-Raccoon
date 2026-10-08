export class NoticeRequestError extends Error {
  constructor(
    public readonly code: "configuration" | "connection" | "contract",
  ) {
    super(code);
    this.name = "NoticeRequestError";
  }
}
export function noticeErrorPresentation(error: unknown) {
  const code = error instanceof NoticeRequestError ? error.code : "connection";
  return code === "configuration"
    ? {
        title: "서비스 연결이 준비되지 않았어요.",
        description: "잠시 후 다시 이용해 주세요.",
        retryable: false,
      }
    : code === "contract"
      ? {
          title: "공문 정보를 확인하지 못했어요.",
          description:
            "서비스 정보를 확인하고 있어요. 잠시 후 다시 이용해 주세요.",
          retryable: false,
        }
      : {
          title: "공문을 불러오지 못했어요.",
          description: "연결 상태를 확인하고 다시 불러와 주세요.",
          retryable: true,
        };
}
