import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { preferenceStorage } from "../../../shared/accessibility/preferenceStorage";

type BookmarkState = {
  savedIds: string[];
  toggleBookmark: (id: string) => void;
};
export const useBookmarkStore = create<BookmarkState>()(
  persist(
    (set) => ({
      savedIds: [],
      toggleBookmark: (id) =>
        set((state) => ({
          savedIds: state.savedIds.includes(id)
            ? state.savedIds.filter((value) => value !== id)
            : [...state.savedIds, id],
        })),
    }),
    {
      name: "wolgyenotice-bookmarks",
      storage: createJSONStorage(() => preferenceStorage),
      partialize: ({ savedIds }) => ({ savedIds }),
      merge: (persisted, current) => {
        const ids =
          persisted && typeof persisted === "object" && "savedIds" in persisted
            ? persisted.savedIds
            : [];
        return {
          ...current,
          savedIds: Array.isArray(ids)
            ? [
                ...new Set(
                  ids.filter(
                    (id): id is string =>
                      typeof id === "string" && /^[1-9]\d*$/.test(id),
                  ),
                ),
              ]
            : [],
        };
      },
    },
  ),
);
