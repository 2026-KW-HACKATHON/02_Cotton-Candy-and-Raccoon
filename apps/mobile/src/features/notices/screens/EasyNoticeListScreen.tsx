import { NoticeQueryFeedback } from "../components/NoticeReadStatus";
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
  const listQuery = useNotices({}, kind !== "saved");
  const savedQuery = useSavedNotices(kind === "saved");
  const query = kind === "saved" ? savedQuery : listQuery;
  const ids = useBookmarkStore((state) => state.savedIds);
  // 편한 화면은 검색·분류 조작을 생략하고 원본을 변경하지 않은 채 게시일순으로 표시한다.
  const notices = [...(query.data ?? [])]
    .filter((notice) => kind !== "saved" || ids.includes(notice.id))
    .sort(
      (a, b) =>
        b.registeredOn.localeCompare(a.registeredOn) ||
        Number(b.id) - Number(a.id),
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
            {home ? "새로 등록된 공문을 확인하세요" : "공문 등록일 최신순"}
          </AppText>
        )}
      </View>
      {home && (
        <AppText size={EASY.heading} variant="display">
          최근 공문
        </AppText>
      )}
      <NoticeQueryFeedback
        error={query.error}
        hasData={query.data !== undefined}
        retry={() => void query.refetch()}
      />
      {query.isPending ? (
        <EasyNoticeState
          loading={query.isPending}
          error={query.isError}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : query.isError && query.data === undefined ? null : notices.length ? (
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
              ? "등록된 공문이 없습니다"
              : kind === "saved"
                ? "저장한 공문이 없습니다. 상세에서 저장하기를 눌러주세요."
                : "등록된 공문이 없습니다"
          }
        />
      )}
      {kind !== "saved" && listQuery.hasNextPage && (
        <EasyButton
          label={listQuery.isFetchingNextPage ? "불러오는 중" : "더 보기"}
          disabled={listQuery.isFetchingNextPage}
          onPress={() => void listQuery.fetchNextPage()}
        />
      )}
      {home && (
        <EasyButton
          label="전체 공문 보기"
          onPress={() => router.navigate("/notices")}
        />
      )}
      <AppText size={14} secondary>
        공식 기관에서 제공한 공지입니다.
      </AppText>
    </Screen>
  );
}
