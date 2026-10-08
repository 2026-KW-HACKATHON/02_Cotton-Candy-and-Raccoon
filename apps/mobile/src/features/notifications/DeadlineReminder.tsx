import { useEffect, useRef, useState } from "react";
import {
  AppState,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { AppText } from "@/shared/ui/AppText";
import { COLORS } from "@/shared/theme/tokens";
import type { Notice } from "../notices/types/notice";
import { dateInput, parseReminderDate, presetDate } from "./reminderDate";
import {
  cancelReminder,
  getReminder,
  scheduleReminder,
} from "./reminderService";

export function DeadlineReminder({ notice }: { notice: Notice }) {
  const [date, setDate] = useState("");
  const [time, setTime] = useState("09:00");
  const [saved, setSaved] = useState<Date | null>(null);
  const [delayed, setDelayed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const inFlight = useRef(false);
  const revision = useRef(0);
  const certainDeadline =
    notice.summaryStatus === "summarized" ? notice.deadlineDate : undefined;
  useEffect(() => {
    if (Platform.OS !== "android") return;
    let active = true;
    const read = async () => {
      const request = ++revision.current;
      const current = () => active && request === revision.current;
      try {
        const result = await getReminder(notice.id);
        if (current()) {
          setSaved(result);
          setDelayed(result !== null && result.getTime() <= Date.now());
        }
      } catch {
        if (current())
          setMessage("예약 정보를 확인하지 못했어요. 화면을 다시 열어 주세요.");
      } finally {
        if (current()) setLoading(false);
      }
    };
    void read();
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active" && !inFlight.current) void read();
    });
    return () => {
      active = false;
      subscription.remove();
    };
  }, [notice.id]);
  if (Platform.OS !== "android") return null;

  const perform = async (cancel: boolean) => {
    if (inFlight.current) return;
    // Invalidate reads started before this mutation, including late errors.
    ++revision.current;
    inFlight.current = true;
    setBusy(true);
    setMessage("");
    try {
      if (cancel) {
        await cancelReminder(notice.id);
        setSaved(null);
        setDelayed(false);
        setMessage("예약을 취소했어요.");
      } else {
        const selected = parseReminderDate(date, time);
        await scheduleReminder(notice.id, notice.title, selected);
        setSaved(selected);
        setDelayed(false);
        setMessage(
          "알림을 예약했어요. 변경하려면 새 날짜와 시간으로 다시 예약해 주세요.",
        );
      }
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "알림 설정에 실패했어요. 다시 시도해 주세요.",
      );
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  const choosePreset = (days: number) => {
    try {
      setDate(presetDate(certainDeadline!, days));
      setMessage("");
    } catch {
      setMessage("마감 날짜를 확인하고 직접 입력해 주세요.");
    }
  };
  return (
    <View style={styles.card}>
      <AppText variant="bold">마감 알림</AppText>
      <AppText>
        원문에서 마감일을 확인한 뒤 알림 시간을 정해 주세요. 시간은 휴대폰 현지
        시각 기준이에요.
      </AppText>
      <AppText accessibilityLiveRegion="polite">
        {loading
          ? "예약 확인 중…"
          : saved
            ? `예약: ${dateInput(saved)} ${String(saved.getHours()).padStart(2, "0")}:${String(saved.getMinutes()).padStart(2, "0")}`
            : "예약된 알림이 없어요."}
      </AppText>
      {saved && delayed && (
        <AppText>
          예정 시각이 지났지만 기기에 예약이 남아 있어요. 수신이 지연될 수
          있으며 예약을 취소할 수 있어요.
        </AppText>
      )}
      {certainDeadline ? (
        <View style={styles.row}>
          <Button
            label="마감 하루 전"
            disabled={busy || loading}
            onPress={() => choosePreset(1)}
          />
          <Button
            label="마감 당일"
            disabled={busy || loading}
            onPress={() => choosePreset(0)}
          />
        </View>
      ) : (
        <AppText>
          확인된 마감일이 없어요. 원문을 보고 날짜를 직접 지정해 주세요.
        </AppText>
      )}
      <AppText>알림 날짜 · YYYY-MM-DD</AppText>
      <TextInput
        accessibilityLabel="알림 날짜, 예: 2026-10-09"
        placeholder="2026-10-09"
        value={date}
        onChangeText={setDate}
        editable={!busy}
        maxLength={10}
        style={styles.input}
      />
      <AppText>알림 시간 · 24시간 HH:mm</AppText>
      <TextInput
        accessibilityLabel="알림 시간, 예: 09:00"
        placeholder="09:00"
        value={time}
        onChangeText={setTime}
        editable={!busy}
        maxLength={5}
        style={styles.input}
      />
      <Button
        label={
          busy
            ? "처리 중…"
            : saved
              ? "선택한 시간으로 변경"
              : "선택한 시간에 알림 받기"
        }
        disabled={busy || loading}
        onPress={() => void perform(false)}
      />
      {saved && (
        <Button
          label="예약 취소"
          disabled={busy || loading}
          onPress={() => void perform(true)}
        />
      )}
      {!!message && (
        <AppText accessibilityLiveRegion="polite">{message}</AppText>
      )}
      <Button
        label="휴대폰 알림 설정 열기"
        disabled={busy}
        onPress={() => {
          void Linking.openSettings().catch(() =>
            setMessage("휴대폰 설정에서 앱 알림을 확인해 주세요."),
          );
        }}
      />
      <AppText secondary>
        알림이 꺼져 있거나 절전 설정에 따라 수신이 늦어질 수 있어요. 정확한
        시각이 필요하면 휴대폰의 ‘알람 및 리마인더’ 권한도 확인해 주세요.
      </AppText>
    </View>
  );
}

function Button({
  label,
  disabled,
  onPress,
}: {
  label: string;
  disabled?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={[styles.button, disabled && { opacity: 0.5 }]}
    >
      <AppText variant="bold">{label}</AppText>
    </Pressable>
  );
}
const styles = StyleSheet.create({
  card: {
    padding: 20,
    gap: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: 16,
  },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  input: {
    minHeight: 48,
    padding: 12,
    fontSize: 18,
    color: COLORS.secondary,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: 8,
  },
  button: {
    minHeight: 48,
    padding: 12,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 8,
    backgroundColor: COLORS.soft,
  },
});
