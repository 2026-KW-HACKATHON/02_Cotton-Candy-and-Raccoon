import type {
  DictionaryEntry,
  DictionaryState,
  DocumentPart,
  GlossaryTerm,
  Notice,
} from "../types/notice";

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function nonempty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
function entries(value: unknown): DictionaryEntry[] | null {
  if (!Array.isArray(value) || !value.length) return null;
  const result: DictionaryEntry[] = [];
  for (const raw of value) {
    const entry = object(raw);
    if (
      !entry ||
      !nonempty(entry.headword) ||
      !Array.isArray(entry.senses) ||
      !entry.senses.length
    )
      return null;
    try {
      const url = new URL(String(entry.source_url));
      if (
        url.protocol !== "https:" ||
        url.host !== "stdict.korean.go.kr" ||
        url.username ||
        url.password ||
        url.hash ||
        url.pathname !== "/search/searchView.do" ||
        typeof entry.target_code !== "string" ||
        !/^\d+$/.test(entry.target_code) ||
        url.searchParams.getAll("word_no").length !== 1 ||
        url.searchParams.get("word_no") !== entry.target_code
      )
        return null;
      const senses = [];
      for (const rawSense of entry.senses) {
        const sense = object(rawSense);
        if (
          !sense ||
          !nonempty(sense.definition) ||
          !nonempty(sense.part_of_speech)
        )
          return null;
        senses.push({
          definition: sense.definition,
          partOfSpeech: sense.part_of_speech,
        });
      }
      result.push({ headword: entry.headword, sourceUrl: url.href, senses });
    } catch {
      return null;
    }
  }
  return result;
}

/** RPC offsets count Unicode code points in title + newline + body, not UTF-16. */
export function withNoticeDictionary(
  notice: Notice,
  payload: unknown,
  state: DictionaryState = "complete",
): Notice {
  const plain = [{ text: notice.original }];
  const output: Notice = {
    ...notice,
    dictionaryStatus: state,
    documentParts: {
      original: plain,
      easy: notice.documentParts?.easy ?? [{ text: notice.easy }],
    },
  };
  if (state === "loading" || state === "failed" || state === "unprocessed")
    return output;
  if (payload === null || payload === undefined) {
    output.dictionaryStatus = "unprocessed";
    return output;
  }
  const data = object(payload);
  const source = notice.title + "\n" + notice.original;
  if (
    !data ||
    data.notice_id !== Number(notice.id) ||
    data.original_text !== source ||
    !["complete", "partial", "pending", "unprocessed"].includes(
      String(data.dictionary_status),
    )
  ) {
    output.dictionaryStatus = "failed";
    return output;
  }
  output.dictionaryStatus = data.dictionary_status as DictionaryState;
  if (
    data.dictionary_candidates === null &&
    data.dictionary_status !== "complete"
  )
    return output;
  if (!Array.isArray(data.dictionary_candidates)) {
    output.dictionaryStatus = "failed";
    return output;
  }
  const points = Array.from(source);
  const parts: DocumentPart[] = [];
  let offset = Array.from(notice.title + "\n").length;
  for (const raw of data.dictionary_candidates) {
    const candidate = object(raw);
    const start = candidate?.start,
      end = candidate?.end;
    if (
      !candidate ||
      typeof start !== "number" ||
      typeof end !== "number" ||
      !Number.isSafeInteger(start) ||
      !Number.isSafeInteger(end) ||
      start < offset ||
      end <= start ||
      end > points.length ||
      !nonempty(candidate.original) ||
      points.slice(start, end).join("") !== candidate.original ||
      !nonempty(candidate.query_word) ||
      !["found", "not_found", "pending", "failed"].includes(
        String(candidate.lookup_status),
      )
    ) {
      output.dictionaryStatus = "failed";
      return output; // Never guess positions or retain a partially invalid mapping.
    }
    let status = candidate.lookup_status as NonNullable<
      GlossaryTerm["dictionary"]
    >["status"];
    let definitions: DictionaryEntry[] = [];
    if (status === "found" || status === "not_found") {
      const dictionary = object(candidate.dictionary);
      const valid =
        dictionary?.query_word === candidate.query_word &&
        dictionary?.contract_version === "stdict-v1" &&
        dictionary?.status === status;
      const parsed =
        status === "found" && valid ? entries(dictionary?.entries) : null;
      if (status === "found" && parsed) definitions = parsed;
      else if (
        status !== "not_found" ||
        !valid ||
        !Array.isArray(dictionary?.entries) ||
        dictionary.entries.length !== 0
      )
        status = "failed";
    }
    if (status === "failed") output.dictionaryStatus = "partial";
    else if (status === "pending" && output.dictionaryStatus === "complete")
      output.dictionaryStatus = "pending";
    if (start > offset)
      parts.push({ text: points.slice(offset, start).join("") });
    parts.push({
      text: candidate.original,
      term: {
        original: candidate.original,
        plain: "",
        dictionary: {
          key: `${start}:${end}:${candidate.original}`,
          queryWord: candidate.query_word,
          status,
          entries: definitions,
        },
      },
    });
    offset = end;
  }
  if (offset < points.length)
    parts.push({ text: points.slice(offset).join("") });
  output.documentParts!.original = parts;
  return output;
}

export function dictionaryHint(notice: Notice): string {
  const hasTerms = notice.documentParts?.original.some(
    (part) => part.term?.dictionary,
  );
  switch (notice.dictionaryStatus) {
    case "loading":
      return "단어 뜻을 불러오고 있어요.";
    case "pending":
      return hasTerms
        ? "일부 단어 뜻을 준비 중이에요. 밑줄 친 단어를 눌러 확인해 주세요."
        : "이 공문의 사전 뜻을 준비 중이에요.";
    case "failed":
      return "단어 뜻을 불러오지 못했어요. 원문은 계속 읽을 수 있어요.";
    case "partial":
      return "일부 단어 뜻을 확인하지 못했어요. 밑줄 친 단어를 눌러 확인해 주세요.";
    case "unprocessed":
      return notice.hasEasyText
        ? "이 공문의 사전 설명은 아직 준비되지 않았어요."
        : "쉬운말과 사전 설명은 아직 준비되지 않았어요. 원문으로 확인해 주세요.";
    default:
      return notice.documentParts?.original.some(
        (part) => part.term?.dictionary,
      )
        ? "밑줄 친 단어를 누르면 사전 뜻을 볼 수 있어요."
        : "이 공문에는 사전 설명이 연결된 단어가 없어요.";
  }
}

/** Resolve again after refetch so an open sheet never keeps stale definitions. */
export function currentDictionaryTerm(
  notice: Notice | null | undefined,
  term: GlossaryTerm | null,
): GlossaryTerm | null {
  if (!term?.dictionary) return notice?.hasEasyText ? term : null;
  return (
    notice?.documentParts?.original.find(
      (part) => part.term?.dictionary?.key === term.dictionary!.key,
    )?.term ?? null
  );
}
