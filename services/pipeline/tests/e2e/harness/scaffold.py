"""Create a draft e2e case from saved Nowon API and page responses.

    uv run python tests/e2e/harness/scaffold.py --name <case> --source nowon \\
        --api <list.xml> [--page <page.html> | --page <post_sn>=<page.html> ...] \\
        [--file <file_name>=<path> ...] [--easy-text]

The draft runs one `collect --source nowon --mode new` step. It copies inputs into
cases/<case>/input/, registers every URL the collector will request, and prints a
TODO for each response that still has to be supplied. Review case.json, then run
`E2E_UPDATE=1 uv run pytest tests/e2e -k <case>` and read expected/ before committing.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from pipeline.attachments.nowon_html import (
    AttachmentError,
    extract_files,
    extract_page_files,
    merge_files,
    recover_masked_body_urls,
)
from pipeline.sources.nowon_api import NowonSourceError, parse_page
from pipeline.sources.nowon_page import NowonPageError, normalize_nowon_notice_url

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"
FIRST_PAGE = 50
# Masked personal names as published by the boards, e.g. 이0진, 김○수, 박*희.
MASKED_NAME = re.compile(r"[가-힣][0O○◯●*＊][가-힣]")
PHONE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", required=True, help="case folder name (snake_case)")
    parser.add_argument("--source", required=True, choices=["nowon"])
    parser.add_argument("--api", required=True, type=Path, help="NowonNewsNoticeList XML")
    parser.add_argument("--page", action="append", default=[], help="[post_sn=]page.html")
    parser.add_argument("--file", action="append", default=[], help="file_name=path")
    parser.add_argument("--easy-text", action="store_true", help="add --easy-text to the step")
    parser.add_argument("--force", action="store_true", help="replace an existing draft")
    parser.add_argument("--cases-dir", type=Path, default=CASES_DIR, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _pairs(values: list[str], option: str) -> dict[str, Path]:
    pairs: dict[str, Path] = {}
    for value in values:
        key, sep, path = value.partition("=")
        if not sep:
            raise SystemExit(f"{option} expects <key>=<path>: {value}")
        pairs[key] = Path(path)
    return pairs


def _warn_personal_data(label: str, text: str) -> list[str]:
    found = sorted(set(MASKED_NAME.findall(text)) | set(PHONE.findall(text)))
    return [f"{label}: 개인정보로 보이는 값 {', '.join(found)}"] if found else []


def scaffold(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not re.fullmatch(r"[a-z0-9_]+", args.name):
        raise SystemExit("--name은 영어 소문자, 숫자, 밑줄만 사용합니다.")
    case_dir = args.cases_dir / args.name
    if case_dir.exists() and not args.force:
        raise SystemExit(f"{case_dir} 가 이미 있습니다. 덮어쓰려면 --force를 쓰세요.")

    xml = args.api.read_bytes()
    try:
        page = parse_page(xml, start_index=1, end_index=FIRST_PAGE)
    except NowonSourceError as error:
        raise SystemExit(f"API 응답을 읽을 수 없습니다: {error}") from None
    notices = page.notices
    todos: list[str] = []
    warnings = _warn_personal_data(args.api.name, xml.decode("utf-8-sig"))
    if page.total_count > FIRST_PAGE:
        todos.append(
            f"총 {page.total_count}건: 최초 수집은 50건까지 읽고 끝나지만, 다음 페이지 라우트가 "
            "필요한지 확인하세요"
        )

    page_args = [p for p in args.page if "=" not in p]
    pages = _pairs([p for p in args.page if "=" in p], "--page")
    if page_args:
        if len(page_args) != 1 or len(notices) != 1:
            raise SystemExit("공지가 여러 건이면 --page <post_sn>=<page.html> 형식을 쓰세요.")
        pages[notices[0].post_sn] = Path(page_args[0])
    files = _pairs(args.file, "--file")

    if case_dir.exists():
        shutil.rmtree(case_dir)
    (case_dir / "input").mkdir(parents=True)
    shutil.copyfile(args.api, case_dir / "input" / "nowon_list.xml")
    routes: list[dict[str, object]] = [
        {"route": "nowon_api", "start": 1, "end": FIRST_PAGE, "body": "input/nowon_list.xml"}
    ]
    easy_text: list[str] = []
    used_files: set[str] = set()
    for notice in notices:
        try:
            page_url = normalize_nowon_notice_url(notice)
        except NowonPageError as error:
            todos.append(f"{notice.post_sn}: 원문 URL이 수집기 검증을 통과하지 못합니다 ({error})")
            continue
        source = pages.get(notice.post_sn)
        body_name = f"input/nowon_page_{notice.post_sn}.html"
        if source is None:
            todos.append(f"{notice.post_sn}: 원문 페이지 HTML을 {body_name}에 넣으세요")
            (case_dir / body_name).write_text("", encoding="utf-8")
            page_html = ""
        else:
            shutil.copyfile(source, case_dir / body_name)
            page_html = source.read_text(encoding="utf-8")
            warnings += _warn_personal_data(source.name, page_html)
        routes.append({"route": "nowon_page", "post_sn": notice.post_sn, "body": body_name})
        if page_html:
            try:
                recovered = recover_masked_body_urls(notice, page_html)
                found = merge_files(
                    extract_files(recovered), extract_page_files(recovered, page_html, page_url)
                )
            except AttachmentError as error:
                todos.append(f"{notice.post_sn}: 첨부 목록을 읽지 못했습니다 ({error.code})")
                found = []
            for index, record in enumerate(found, 1):
                name = record.file_name or f"{record.kind}_{index}"
                warnings += _warn_personal_data(f"{notice.post_sn} 파일명", name)
                body = f"input/{notice.post_sn}_{index}{Path(name).suffix}"
                # Collection stores file metadata only, so no file route is registered here.
                if name in files:
                    shutil.copyfile(files[name], case_dir / body)
                    used_files.add(name)
                    todos.append(
                        f"{notice.post_sn}: '{name}'을 {body}에 복사했습니다. 내려받는 step에 "
                        + json.dumps({"route": "file", "url": record.url, "body": body},
                                     ensure_ascii=False)
                        + " 라우트를 추가하세요"
                    )
        if args.easy_text:
            name = f"input/easy_{notice.post_sn}.json"
            (case_dir / name).write_text(
                '{"changes": [], "dictionary_candidates": []}\n', encoding="utf-8"
            )
            easy_text.append(name)
            todos.append(f"{notice.post_sn}: {name}를 실제 Gemini 쉬운말 응답으로 바꾸세요")
    for name in sorted(set(files) - used_files):
        todos.append(f"--file {name}: 이 이름의 첨부가 응답에 없습니다")

    step: dict[str, object] = {
        "type": "collect",
        "args": ["--source", "nowon", "--mode", "new", *(["--easy-text"] if easy_text else [])],
        "http": routes,
    }
    if easy_text:
        step["gemini"] = {"easy_text": easy_text}
    step["expect_exit"] = 0
    case = {"title": "TODO: 이 케이스가 확인하는 것", "steps": [step], "strict_unused": True}
    (case_dir / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"케이스 초안: {case_dir}")
    for line in warnings:
        print(f"경고: {line}. 가짜 값으로 바꾸세요.", file=sys.stderr)
    for line in ["case.json의 title을 채우세요", *todos]:
        print(f"TODO: {line}")
    print(f"다음: E2E_UPDATE=1 uv run pytest tests/e2e -k {args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(scaffold())
