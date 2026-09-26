# Gemini 공지 요약 입력과 실행

`services/pipeline`의 요약기는 공지 한 건을 받아 Gemini 응답을 검증합니다. 수집기·DB와의 연결은 별도 작업입니다.

## 코드 구성

실행 흐름은 `summary_cli.py` → `notice_input.py` → `summarize.py` → `gemini_client.py` → `summary_schema.py`·`grounding.py` → 결과 JSON입니다. `summarize.py`는 중간에 `gemini_prompt.py`에서 프롬프트와 API 키를 읽습니다.

| 파일 | 역할 |
| --- | --- |
| [`pipeline/__init__.py`](../services/pipeline/src/pipeline/__init__.py) | 파이썬 패키지를 표시합니다. 실행 로직은 없습니다. |
| [`notice_input.py`](../services/pipeline/src/pipeline/transform/notice_input.py) | 공지 입력 형식과 필수 값을 검사하고, 본문·첨부 추출 텍스트를 Gemini에 보낼 자료로 만듭니다. |
| [`gemini_prompt.py`](../services/pipeline/src/pipeline/transform/gemini_prompt.py) | 요약 프롬프트 MD와 `GEMINI_API_KEY`를 환경변수 또는 로컬 `.env`에서 읽습니다. |
| [`gemini_client.py`](../services/pipeline/src/pipeline/transform/gemini_client.py) | Gemini API를 호출하고 응답 JSON 문자열을 받습니다. 요청 시간 제한과 API 오류를 처리합니다. |
| [`summary_schema.py`](../services/pipeline/src/pipeline/transform/summary_schema.py) | 출력 필드·코드값·15자 제한·날짜 형식을 정의하고 원문 발췌의 존재 여부를 검사합니다. |
| [`grounding.py`](../services/pipeline/src/pipeline/transform/grounding.py) | 출력 내용을 원문과 대조해 근거가 부족한 항목을 비우고 `원문 확인 필요`를 남깁니다. |
| [`summarize.py`](../services/pipeline/src/pipeline/transform/summarize.py) | 입력 준비부터 호출·검증까지 연결합니다. JSON 형식 오류에는 한 번 재요청하고, 남은 오류는 확인 가능한 필드만 유지합니다. |
| [`summary_cli.py`](../services/pipeline/src/pipeline/transform/summary_cli.py) | 터미널에서 입력 JSON 파일 한 건을 받아 요약기를 실행하고 결과 JSON을 출력합니다. |
| [`test_gemini_summary.py`](../services/pipeline/tests/test_gemini_summary.py) | 위 기능의 입력·출력 계약과 오류·근거 검증 동작을 테스트합니다. |

Gemini에 전달하는 실제 지시문은 [`prompts/gemini_notice_summary.md`](../services/pipeline/src/pipeline/transform/prompts/gemini_notice_summary.md)에 있습니다. 이 문서(`docs/gemini-summary.md`)는 개발자용 설명서이고 실행 중에는 읽지 않습니다.

## 입력 형식

본문은 **HTML 태그를 제거한 평문**입니다. 첨부파일은 파일별로 텍스트 추출이 끝난 결과만 넣습니다. `reference_datetime`은 상태 판단에 쓰므로 시간대가 포함된 시각을 반드시 전달합니다. 요약기는 이 시각을 한국 시간으로 변환해 모델에 보냅니다.

```json
{
  "title": "월계1동 걷기 행사 참가자 모집",
  "body_text": "10월 5일부터 12일까지 온라인으로 신청하세요.",
  "reference_datetime": "2026-09-25T19:00:00+09:00",
  "attachments": [
    {"name": "안내문.txt", "text": "참가비는 무료입니다."}
  ],
  "publisher": "월계1동 주민센터",
  "department": null,
  "published_on": "2026-09-25"
}
```

필수: `title`, `body_text`, `reference_datetime`. 선택: `attachments`, `publisher`, `department`, `published_on`. `body_text`가 빈 문자열일 수는 있지만, 그러면 모델이 본문 내용을 확인할 수 없습니다. `attachments`의 각 항목에는 비어 있지 않은 `name`과 `text`가 필요합니다. `publisher`는 실제 발행기관이고 `department`는 담당 부서이므로 같은 값으로 추정하지 않습니다.

## 실행

`services/pipeline/.env`에 `GEMINI_API_KEY`를 로컬로 설정합니다. 키 파일은 Git에서 제외됩니다.

```powershell
cd services/pipeline
uv sync
uv run summarize-notice examples/notice.json
```

기본 모델은 `gemini-3.5-flash-lite`입니다. 필요하면 `--model`로 변경할 수 있습니다. 결과는 프롬프트에서 정한 JSON이며, 코드가 필드·열거값·15자 제한·날짜 형식과 `evidence.excerpt`의 실제 원문 포함 여부를 검사합니다. 검증에 실패하면 정상 결과로 내보내지 않습니다. 요청은 `store=False`로 독립 실행합니다.

JSON 형식이나 필드 형식이 잘못되면 오류 위치를 Gemini에 알려주고 한 번만 다시 요청합니다. 두 번째 응답에도 일부 문자열의 길이 오류가 남으면 해당 문자열을 비우고, 확인 가능한 필드를 유지합니다. 원문 근거가 부족한 값은 추가 API 호출 없이 비우고 `uncertainties`에 `"원문 확인 필요"`를 넣습니다. JSON 자체를 해석할 수 없으면 확인되지 않은 내용을 내보내지 않도록 `unknown` 결과로 내려갑니다. 본문과 추출된 첨부 텍스트가 모두 비어 있어도 API를 호출하지 않고 `unknown` 결과를 반환합니다.

행동·대상·장소·유의사항은 원문에 있는 짧은 표현인지 확인하고, 일정의 종류·날짜·시간은 해당 원문 행과 대조합니다. 조건 일부를 빼면 대상이 넓어지는 경우도 비웁니다. 자동 검증으로 모든 문장의 의미까지 보장할 수는 없으므로, 화면 연결 시에는 `notices.url`의 원문 링크를 요약 하단에 표시해야 합니다. 현재 CLI는 JSON 결과만 출력하며 화면이나 원문 링크는 연결하지 않았습니다.

## 반환 정보

공지 한 건마다 아래 필드를 모두 포함한 JSON 객체 하나를 반환합니다. 확인할 수 없는 단일 값은 `null` 또는 `unknown`, 해당 항목이 없는 목록은 `[]`로 표시합니다. `summary` 등 화면용 짧은 문구는 공백 포함 15자 이내입니다.

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `category` | 코드 | 공지 유형: `application` 신청·모집, `event` 행사, `living` 생활 안내, `obligation` 신고·납부·제출, `news` 소식, `mixed` 종합 안내, `unknown` 판별 불가 |
| `summary` | 문자열 | 공지의 핵심 내용 |
| `publisher` | 문자열 또는 `null` | 발행기관 |
| `applicable_area` | 문자열 또는 `null` | 공지가 적용되는 지역·구간 |
| `audience` | 문자열 또는 `null` | 신청·참여·영향 대상 |
| `audience_scope` | 코드 | `general` 일반, `conditional` 자격 조건 있음, `specific` 특정 대상, `unknown` 확인 불가 |
| `action` | 문자열 또는 `null` | 주민이 해야 할 행동과 확인된 방법 |
| `action_requirement` | 코드 | `required` 의무, `optional` 선택, `recommended` 권고, `none` 행동 없음, `unknown` 확인 불가 |
| `location` | 문자열 또는 `null` | 행사·방문 등의 장소 |
| `dates` | 배열 | 신청·행사·운영 등 일정별 항목 |
| `status` | 코드 | `upcoming` 예정, `open` 접수 중, `ongoing` 진행 중, `closed` 접수 종료, `ended` 종료, `cancelled` 취소, `ongoing_intake` 상시 접수, `check_required` 확인 필요, `not_applicable` 해당 없음, `unknown` 확인 불가 |
| `status_detail` | 문자열 또는 `null` | 방식별 상태 차이 등 상태의 보충 설명 |
| `notice_update` | 코드 | `new` 일반 공지, `modified` 정정, `extended` 연장, `cancelled` 취소, `unknown` 확인 불가 |
| `changed_details` | 문자열 또는 `null` | 정정·연장 등에서 바뀐 내용 |
| `notes` | 문자열 배열 | 비용, 자격 제한, 필수 서류, 마감 조건 등 유의사항 |
| `topics` | 객체 배열 | `mixed` 공지에 묶인 사업별 `title`, `category`, `summary` |
| `uncertainties` | 문자열 배열 | 근거가 부족하거나 추가 확인이 필요한 내용 |
| `evidence` | 객체 배열 | 출력 필드명 `field`와 본문·첨부에서 그대로 발췌한 `excerpt` |

`dates`의 각 항목에는 `kind`, `label`, `text`, `start_date`, `end_date`, `start_time`, `end_time`이 들어갑니다. `kind`는 `application` 신청, `event` 행사, `operation` 운영, `payment` 납부, `submission` 제출, `effective` 시행, `disruption` 통제·중단, `result` 결과 발표, `other` 기타 중 하나입니다. 날짜는 `YYYY-MM-DD`, 시간은 `HH:MM` 형식이며, 확인되지 않은 값은 `null`입니다.

## 출력 JSON을 화면에 묶는 방법

요약 결과의 필드는 화면에서 다음 네 영역으로 묶을 수 있습니다. 이는 화면 연동을 위한 제안이며, 현재 CLI가 네 영역을 별도 객체로 출력하지는 않습니다.

| 화면 영역 | 사용할 필드 | 표시할 내용 |
| --- | --- | --- |
| 무슨 공지? | `summary`, `category`, `publisher`, `notice_update`, `changed_details` | 핵심 내용과 유형·발행기관, 정정·연장·취소 여부 |
| 누가 해당돼? | `audience`, `audience_scope`, `applicable_area` | 대상자의 자격 조건과 적용 지역 |
| 무엇을 해야 해? | `action`, `action_requirement`, `location` | 주민의 행동, 필수·선택 여부와 장소 |
| 언제까지야? | `dates`, `status`, `status_detail` | 신청·행사·운영 등 일정별 날짜와 현재 상태 |

`dates`는 여러 일정이 들어갈 수 있으므로 `kind`와 `label`로 신청 기간, 행사일, 운영 기간 등을 구분해 표시합니다. `notes`의 비용·자격·마감 조건은 해당 영역에 붙이고, 어느 영역에도 명확히 속하지 않으면 별도의 유의사항으로 보여줍니다. `category`가 `mixed`이면 `topics`를 사업별로 표시하고 서로 다른 사업의 대상과 일정을 한 조건처럼 합치지 않습니다.

`null`이나 빈 배열인 항목은 추측해 채우지 않습니다. `uncertainties`에 `"원문 확인 필요"`가 있으면 요약 아래에 알리고, 저장된 공지의 `notices.url`을 원문 링크로 제공합니다. `evidence`는 필요할 때 원문 근거를 펼쳐 볼 수 있도록 사용할 수 있습니다. 원문 URL은 요약 JSON의 필드가 아니라 공지 데이터에서 가져와야 합니다.

수집 브랜치가 합쳐지면 수집기의 `body_html`을 평문으로 바꾸고, 첨부파일에서 텍스트를 추출해 이 입력 형식으로 전달해야 합니다. 수집 모델의 `category`는 출처이고 요약 결과의 `category`는 공지 유형이므로 서로 혼동하지 않습니다.

공개 PDF·이미지 링크를 Gemini에 전달하는 기능은 이 단계에 포함되지 않습니다. 해당 링크만 있고 추출 텍스트가 없으면 요약 근거를 검증할 수 없으므로, 파일 접근·본문 추출 또는 파일 근거 검증 정책을 먼저 추가해야 합니다.
