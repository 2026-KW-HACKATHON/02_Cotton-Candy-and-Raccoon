import { View } from "react-native";
import { router } from "expo-router";
import { Screen } from "@/shared/ui/Screen";
import { AppText } from "@/shared/ui/AppText";
import { EasyButton } from "@/shared/ui/EasyButton";
import { EASY } from "@/shared/theme/tokens";
import { useNotices, useSavedNotices } from "../hooks/useNotices";
import { useBookmarkStore } from "../store/bookmarkStore";
import { EasyNoticeCard } from "../components/EasyNoticeCard";
import { EasyNoticeState } from "../components/EasyNoticeState";

export function EasyNoticeListScreen({
  kind,
}: {
  kind: "home" | "notices" | "saved";
}) {
  const ids = useBookmarkStore((state) => state.savedIds);
  const listQuery = useNotices(kind !== "saved");
  const savedQuery = useSavedNotices(ids, kind === "saved");
  const query = kind === "saved" ? savedQuery : listQuery;
  // 편한 화면은 검색·분류 조작을 생략하고 원본을 변경하지 않은 채 게시일순으로 표시한다.
  const notices = [...(query.data ?? [])]
    .filter((notice) => kind !== "saved" || ids.includes(notice.id))
    .sort((a, b) =>
      b.publishedAt
        .replaceAll(" ", "")
        .localeCompare(a.publishedAt.replaceAll(" ", "")),
    );
  const home = kind === "home";
  return (
    <Screen
      contentStyle={{
        paddingHorizontal: EASY.inset,
        paddingTop: EASY.inset,
        gap: EASY.gap,
      }}
    >
      <View style={{ gap: 12 }}>
        {home ? (
          <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
            <AppText size={EASY.title} variant="display">
              월계알리미
            </AppText>
            <View
              style={{
                width: 8,
                height: 8,
                borderRadius: 4,
                backgroundColor: EASY.brandPoint,
              }}
            />
          </View>
        ) : (
          <AppText size={EASY.title} variant="display">
            {kind === "saved" ? "저장한 공문" : "전체 공문"}
          </AppText>
        )}
        {kind !== "saved" && (
          <AppText size={EASY.body} secondary>
            {home ? "새로 등록된 공문을 확인해 주세요." : "공문 등록일 최신순"}
          </AppText>
        )}
      </View>
      {home && (
        <AppText size={EASY.heading} variant="display">
          최근 공문
        </AppText>
      )}
      {query.isError && query.data && (
        <EasyNoticeState
          error
          errorDetail={query.error}
          retrying={query.isFetching}
          retry={() => {
            void query.refetch();
          }}
        />
      )}
      {query.isPending || (query.isError && !query.data) ? (
        <EasyNoticeState
          loading={query.isPending}
          error={query.isError}
          errorDetail={query.error}
          retrying={query.isFetching}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : notices.length ? (
        <View style={{ gap: 16 }}>
          {notices.map((notice) => (
            <EasyNoticeCard
              key={notice.id}
              notice={notice}
              onOpen={() =>
                router.push({
                  pathname: "/notice/[id]",
                  params: { id: notice.id },
                })
              }
            />
          ))}
        </View>
      ) : (
        <EasyNoticeState
          message={
            home
              ? "이번 주 공문 없음"
              : kind === "saved"
                ? "저장한 공문이 없습니다. 상세에서 저장하기를 눌러주세요."
                : "등록된 공문이 없습니다"
          }
        />
      )}
      {home && (
        <EasyButton
          label="전체 공문 보기"
          onPress={() => router.navigate("/notices")}
        />
      )}
      {kind === "notices" && listQuery.hasNextPage && (
        <EasyButton
          label={listQuery.isFetchingNextPage ? "불러오는 중…" : "공문 더 보기"}
          filled
          disabled={listQuery.isFetchingNextPage}
          onPress={() => {
            void listQuery.fetchNextPage();
          }}
        />
      )}
    </Screen>
  );
}
