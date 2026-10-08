import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Pressable,
  StyleSheet,
  Switch,
  TextInput,
  View,
} from "react-native";
import { Bell, Plus, X } from "lucide-react-native";
import { Screen } from "@/shared/ui/Screen";
import { Header } from "@/shared/ui/Header";
import { AppText } from "@/shared/ui/AppText";
import {
  CARD_SHADOW,
  COLORS,
  EASY,
  FONTS,
  RADIUS,
  SPACE,
} from "@/shared/theme/tokens";
import { useDisplayPreferences } from "@/shared/accessibility/displayPreferences";
import { loadKeywords, saveKeywords, type KeywordPreferences } from "./api";
import { addKeyword } from "./keywords";

export function KeywordNotificationsScreen() {
  const easy = useDisplayPreferences((s) => s.mode === "easy");
  const size = easy ? EASY.body : 16;
  const [saved, setSaved] = useState<KeywordPreferences | null>(null);
  const [value, setValue] = useState<KeywordPreferences>({
    enabled: false,
    keywords: [],
  });
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  async function load() {
    setLoading(true);
    setMessage("");
    try {
      const result = await loadKeywords();
      setSaved(result);
      setValue(result);
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "다시 시도해 주세요.",
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    let active = true;
    void loadKeywords()
      .then((result) => {
        if (active) {
          setSaved(result);
          setValue(result);
        }
      })
      .catch((error) => {
        if (active)
          setMessage(
            error instanceof Error ? error.message : "다시 시도해 주세요.",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  const dirty = JSON.stringify(saved) !== JSON.stringify(value);
  function add() {
    try {
      setValue({ ...value, keywords: addKeyword(value.keywords, input) });
      setInput("");
      setMessage("");
    } catch (error) {
      setMessage((error as Error).message);
    }
  }
  async function save() {
    setBusy(true);
    setMessage("");
    try {
      if (value.enabled && !value.keywords.length)
        throw new Error("키워드를 한 개 이상 추가해 주세요.");
      const token = value.enabled
        ? await (await import("./push")).pushToken()
        : null;
      const result = await saveKeywords(value, token);
      setSaved(result);
      setValue(result);
      setMessage("키워드 설정을 저장했어요.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "저장하지 못했어요.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Screen
      header={<Header title="관심 키워드 알림" back />}
      bottomSafe
      contentStyle={{ gap: SPACE.xl }}
    >
      <View style={styles.intro}>
        <View style={styles.icon}>
          <Bell color={COLORS.primary} size={30} />
        </View>
        <AppText variant="display" size={easy ? 28 : 24}>
          관심 있는 공문만 알려드려요
        </AppText>
        <AppText secondary size={size}>
          새 공문의 제목이나 본문에 저장한 키워드가 있으면 알려드려요.
        </AppText>
      </View>
      {loading ? (
        <ActivityIndicator
          accessibilityLabel="키워드 불러오는 중"
          color={COLORS.primary}
        />
      ) : (
        saved && (
          <>
            <View style={styles.panel}>
              <View style={styles.row}>
                <AppText variant="bold" size={size} style={{ flex: 1 }}>
                  키워드 알림 받기
                </AppText>
                <Switch
                  accessibilityLabel="키워드 알림 받기"
                  value={value.enabled}
                  disabled={busy}
                  trackColor={{ true: COLORS.primary }}
                  onValueChange={(enabled) => setValue({ ...value, enabled })}
                />
              </View>
              <AppText secondary size={size}>
                끄면 새 공문 알림을 보내지 않아요.
              </AppText>
            </View>
            <View style={styles.panel}>
              <View style={styles.row}>
                <AppText variant="bold" size={size} style={{ flex: 1 }}>
                  내 관심 키워드
                </AppText>
                <AppText secondary>{value.keywords.length}/10</AppText>
              </View>
              <View style={styles.row}>
                <TextInput
                  accessibilityLabel="새 관심 키워드"
                  value={input}
                  onChangeText={setInput}
                  editable={!busy}
                  placeholder="예: 장학금, 주차, 건강"
                  placeholderTextColor={COLORS.secondary}
                  style={[styles.input, { fontSize: size }]}
                  onSubmitEditing={add}
                  returnKeyType="done"
                />
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="키워드 추가"
                  disabled={busy || !input.trim()}
                  onPress={add}
                  style={styles.add}
                >
                  <Plus size={24} color={COLORS.primary} />
                </Pressable>
              </View>
              <AppText secondary size={easy ? 18 : 13}>
                2~30자로 입력해 주세요. 추가·삭제 후 저장해 주세요.
              </AppText>
              <View style={styles.tags}>
                {value.keywords.map((word) => (
                  <View key={word} style={styles.tag}>
                    <AppText size={size}>{word}</AppText>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`${word} 삭제`}
                      disabled={busy}
                      onPress={() =>
                        setValue({
                          ...value,
                          keywords: value.keywords.filter((w) => w !== word),
                        })
                      }
                      style={styles.remove}
                    >
                      <X size={18} color={COLORS.secondary} />
                    </Pressable>
                  </View>
                ))}
              </View>
              {!value.keywords.length && (
                <AppText secondary size={size}>
                  아직 등록한 키워드가 없어요.
                </AppText>
              )}
            </View>
            <Pressable
              accessibilityRole="button"
              disabled={busy || !dirty}
              onPress={save}
              style={[styles.save, (busy || !dirty) && { opacity: 0.5 }]}
            >
              <AppText variant="bold" size={size} style={{ color: "white" }}>
                {busy ? "저장하고 있어요…" : "설정 저장"}
              </AppText>
            </Pressable>
          </>
        )
      )}
      {!!message && (
        <AppText size={size} accessibilityLiveRegion="polite">
          {message}
        </AppText>
      )}
      {!loading && !saved && (
        <Pressable accessibilityRole="button" onPress={load} style={styles.add}>
          <AppText size={size}>다시 불러오기</AppText>
        </Pressable>
      )}
      <View style={styles.panel}>
        <AppText variant="bold" size={size}>
          알아두세요
        </AppText>
        <AppText secondary size={size}>
          등록한 뒤 새로 수집된 공문부터 알려드려요. 여러 키워드가 맞아도 알림은
          한 번만 보내요.
        </AppText>
        <AppText secondary size={size}>
          PDF·이미지 속 글자는 확인하지 않아요. 앱을 삭제하면 키워드를 다시
          등록해야 해요.
        </AppText>
        <Pressable
          accessibilityRole="button"
          onPress={() =>
            void Linking.openSettings().catch(() =>
              setMessage("휴대전화 설정에서 앱 알림을 확인해 주세요."),
            )
          }
        >
          <AppText size={size} style={{ color: COLORS.primary }}>
            휴대전화 알림 설정 열기
          </AppText>
        </Pressable>
      </View>
    </Screen>
  );
}
const styles = StyleSheet.create({
  intro: { gap: 12, paddingVertical: 12 },
  icon: {
    backgroundColor: COLORS.soft,
    borderRadius: 18,
    padding: 14,
    alignSelf: "flex-start",
  },
  panel: {
    backgroundColor: COLORS.surface,
    borderColor: COLORS.border,
    borderWidth: 1,
    borderRadius: RADIUS.card,
    padding: 18,
    gap: 14,
    ...CARD_SHADOW,
  },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  input: {
    flex: 1,
    minWidth: 0,
    minHeight: 52,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: RADIUS.control,
    padding: 12,
    fontFamily: FONTS.body,
    color: COLORS.text,
  },
  add: {
    minHeight: 48,
    minWidth: 48,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: COLORS.soft,
    borderRadius: RADIUS.control,
  },
  tags: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  tag: {
    maxWidth: "100%",
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: COLORS.soft,
    borderRadius: RADIUS.pill,
    paddingLeft: 14,
  },
  remove: {
    minWidth: 44,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  save: {
    minHeight: 56,
    borderRadius: RADIUS.control,
    backgroundColor: COLORS.primary,
    alignItems: "center",
    justifyContent: "center",
  },
});
