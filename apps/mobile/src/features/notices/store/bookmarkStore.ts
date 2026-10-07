import { create } from "zustand";

type BookmarkState = {
  savedIds: string[];
  toggleBookmark: (id: string) => void;
};
// 목록·상세·보관함이 ID만 공유한다. 저장 전에는 비어 있으며 재실행 시 초기화된다.
export const useBookmarkStore = create<BookmarkState>((set) => ({
  savedIds: [],
  toggleBookmark: (id) =>
    set((state) => ({
      savedIds: state.savedIds.includes(id)
        ? state.savedIds.filter((value) => value !== id)
        : [...state.savedIds, id],
    })),
}));
