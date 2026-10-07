import Storage from "expo-sqlite/kv-store";
import { type StateStorage } from "zustand/middleware";

// 네이티브 저장소는 플랫폼 파일에서만 import해 웹에 SQLite 의존성이 전달되지 않게 한다.
export const preferenceStorage: StateStorage = Storage;
