import { create } from "zustand";
import { type StateStorage } from "zustand/middleware";

export const usePreferenceStorageStatus = create(() => ({ available: true }));

/** 저장 실패 후에는 오래된 디스크 값 대신 이번 실행의 설정을 유지한다. */
export function createSafePreferenceStorage(
  getStorage: () => StateStorage | null,
): StateStorage {
  const memory = new Map<string, string>();
  let available = true;
  function fallBackToMemory() {
    available = false;
    usePreferenceStorageStatus.setState({ available: false });
  }
  return {
    getItem: async (name) => {
      if (available) {
        try {
          const storage = getStorage();
          if (storage) {
            const value = await storage.getItem(name);
            if (value !== null) memory.set(name, value);
            else memory.delete(name);
            return value;
          }
        } catch {
          fallBackToMemory();
        }
      }
      return memory.get(name) ?? null;
    },
    setItem: async (name, value) => {
      memory.set(name, value);
      if (available) {
        try {
          await getStorage()?.setItem(name, value);
        } catch {
          fallBackToMemory();
        }
      }
    },
    removeItem: async (name) => {
      memory.delete(name);
      if (available) {
        try {
          await getStorage()?.removeItem(name);
        } catch {
          fallBackToMemory();
        }
      }
    },
  };
}
