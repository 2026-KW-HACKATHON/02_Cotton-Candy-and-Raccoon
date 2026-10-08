const { test, afterEach } = require("node:test");
const assert = require("node:assert/strict");
require("./loadTypeScript.cjs");
const {
  filePreviewKind,
} = require("../src/features/notices/domain/noticeFiles.ts");
const {
  fetchNoticeFilePreviewKind,
} = require("../src/features/notices/api/noticeFileMetadata.ts");

const file = {
  id: 5,
  kind: "attachment",
  url: "https://www.nowon.kr/component/file/ND_fileDownload.do?q_fileSn=7&q_fileId=file-id",
};
const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

test("확장자 없는 첨부는 형식 미확인 상태이고 MIME으로 PDF와 이미지를 판별한다", () => {
  assert.equal(filePreviewKind(file), "unknown");
  assert.equal(
    filePreviewKind(file, { contentType: "Application/PDF; charset=binary" }),
    "pdf",
  );
  for (const mime of [
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
  ]) {
    assert.equal(filePreviewKind(file, { contentType: mime }), "image");
  }
  assert.equal(
    filePreviewKind(file, { contentType: "application/vnd.hancom.hwpx" }),
    "unsupported",
  );
});

test("일반 바이너리 응답은 Content-Disposition의 파일명으로 판별한다", () => {
  assert.equal(
    filePreviewKind(file, {
      contentType: "application/octet-stream",
      contentDisposition: 'attachment; filename="report.PDF"',
    }),
    "pdf",
  );
  assert.equal(
    filePreviewKind(file, {
      contentType: "application/force-download",
      contentDisposition:
        "attachment; filename=fallback.hwp; filename*=UTF-8''%EA%B3%B5%EA%B3%A0.png",
    }),
    "image",
  );
  assert.equal(
    filePreviewKind(file, {
      contentDisposition: 'attachment; filename="report.hwpx"',
    }),
    "unsupported",
  );
  assert.equal(
    filePreviewKind(file, { contentType: "application/octet-stream" }),
    "unknown",
  );
});

test("잘못 인코딩된 응답 파일명은 일반 파일명으로 대체하고 URL 기반 판별도 유지한다", () => {
  assert.equal(
    filePreviewKind(file, {
      contentDisposition:
        "attachment; filename=report.pdf; filename*=UTF-8''%broken",
    }),
    "pdf",
  );
  assert.equal(
    filePreviewKind({ ...file, url: "https://example.test/report.pdf" }),
    "pdf",
  );
  assert.equal(
    filePreviewKind({ ...file, url: "https://example.test/report.hwpx" }),
    "unsupported",
  );
  assert.equal(filePreviewKind({ ...file, kind: "inline_image" }), "image");
});

test("파일 본문 없이 HEAD 응답 헤더로 실제 다운로드 주소의 PDF를 확인한다", async () => {
  global.fetch = async (url, options) => {
    assert.equal(url, file.url);
    assert.equal(options.method, "HEAD");
    assert.ok(options.signal instanceof AbortSignal);
    return new Response(null, {
      headers: { "content-type": "application/pdf" },
    });
  };
  assert.equal(await fetchNoticeFilePreviewKind(file), "pdf");
});

test("HEAD 실패·HTML 오류 페이지·CORS 실패를 미지원 파일로 판정하지 않는다", async () => {
  global.fetch = async () => new Response(null, { status: 405 });
  await assert.rejects(fetchNoticeFilePreviewKind(file), /metadata_failed/);
  global.fetch = async () =>
    new Response(null, {
      headers: { "content-type": "text/html; charset=UTF-8" },
    });
  await assert.rejects(fetchNoticeFilePreviewKind(file), /metadata_failed/);
  global.fetch = async () => {
    throw new TypeError("CORS blocked");
  };
  await assert.rejects(fetchNoticeFilePreviewKind(file), /CORS blocked/);
});

test("형식 조회는 15초 제한 및 호출자의 취소 신호로 중단한다", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  global.fetch = (_url, { signal }) =>
    new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
  const timeout = assert.rejects(fetchNoticeFilePreviewKind(file), /aborted/);
  t.mock.timers.tick(15_000);
  await timeout;
  const controller = new AbortController();
  const cancelled = assert.rejects(
    fetchNoticeFilePreviewKind(file, controller.signal),
    /aborted/,
  );
  controller.abort();
  await cancelled;
});

test("공식 HTTP 주소가 아닌 값은 외부 조회를 실행하지 않는다", async () => {
  global.fetch = () => {
    assert.fail("invalid URL must not be fetched");
  };
  await assert.rejects(
    fetchNoticeFilePreviewKind({ ...file, url: "file:///tmp/report.pdf" }),
    /invalid_url/,
  );
});
