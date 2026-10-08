# 오늘의 공문 Android 위젯 (#88)

메인 화면 `LetterIllustration`의 너구리·편지지·봉투 그림을 재사용해 공문 한 건을 표시합니다. 목록, 요약 생성, 알림, iOS 위젯은 포함하지 않습니다. 위젯 그림은 정적인 모습이며 앱의 연속 애니메이션을 재생하지 않습니다.

## 조회와 갱신

- `app_notice_list` 공개 뷰에서 한국 시간 오늘의 `registered_on`을 조회합니다. 같은 날짜에서는 기존 목록과 동일하게 `id DESC`로 1건을 선택합니다. 원문에 등록 시각이 없으므로 하루 중 실제 게시 시각 순서를 뜻하지 않습니다.
- 요약 생성 여부는 필터링하지 않습니다. 앱과 동일한 `EXPO_PUBLIC_SUPABASE_URL`, `EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY`를 사용합니다.
- 추가·크기 변경·자동 갱신·봉투의 ‘새 공문 확인’ 버튼에서 조회합니다. 위젯 요청은 8초에 중단해, 자정 재조회까지 수행해도 30초 백그라운드 작업 제한 안에 오류 상태를 렌더링할 여유를 둡니다.
- 30분 주기 갱신을 요청하지만 Android 절전/런처 정책에 따라 지연될 수 있습니다. 자정 정각 갱신은 보장하지 않습니다. 이미지에 절대 날짜를 표시하고 갱신 시 이전 응답을 재사용하지 않습니다.
- 빈 응답과 오류를 구분하고 조회 도중 자정이 지나면 새 날짜로 재조회합니다. 다음 갱신 전까지 이전 렌더링이 남을 수 있습니다. 원격 숨김 즉시 반영도 보장하지 않습니다.

## 빌드와 확인

Expo Go는 Android 위젯을 지원하지 않습니다. 공개 환경 변수를 설정한 Android 네이티브 빌드를 설치해야 합니다.

```sh
npm ci
npx tsc --noEmit
npm run lint
node --test tests/*.test.cjs
npx expo run:android
```

로컬 빌드에는 프로젝트 Expo 버전에서 요구하는 JDK와 Android SDK가 필요합니다. 배포 APK에서는 JS 번들을 포함해 Metro 없이 확인합니다. 개발 빌드는 Metro 연결이 필요할 수 있습니다.

1. Android 홈 화면을 길게 눌러 위젯 목록에서 ‘월계 공지 → 오늘의 공문’을 추가합니다.
2. 너구리가 편지를 들고 있는 모습, 제목 잘림 처리, 날짜·출처를 확인합니다.
3. 앱 화면을 닫은 상태에서 갱신 버튼을 누르고 실제 공개 공지와 비교합니다.
4. 긴 제목·큰 글꼴·위젯 크기 변경·공지 없음·오프라인·날짜 변경을 확인합니다.
5. 앱 강제 중지 상태는 일반적인 앱 화면 종료와 구분해 기록합니다.

그림 변경 시 `node scripts/build-widget-art.cjs`로 생성 파일을 갱신합니다. 원본 SVG는 `assets/figma`, 생성된 문자열은 `src/features/widget/letterArtwork.ts`입니다.

`assets/widget/preview.png`는 위젯 선택 화면의 예시 이미지입니다. 예시 공문과 날짜를 사용한 디자인 미리보기이며 실기기 캡처가 아닙니다. 실제 내용은 위젯 추가 후 공개 API로 조회합니다.

실기기 검증은 별도로 필요하며, TypeScript/단위 테스트 통과만으로 Android 표시·백그라운드 실행을 확인한 것으로 간주하지 않습니다.

## 구현 시 검증 결과

- TypeScript 및 Expo ESLint 통과
- 모바일 Node 테스트 33개 통과
- React Compiler 적용 후 실제 라이브러리의 위젯 트리 생성 및 루트 접근성 라벨 검증
- 동시 갱신, 위젯별 독립 갱신, 삭제 후 이전 응답 무시 검증
- Android Expo prebuild 통과: 위젯 receiver, 30분 갱신 설정, 선택 화면 preview 생성 확인
- Android Metro/Hermes 번들 export 통과
- 예시 이미지 렌더링으로 너구리·편지지·텍스트 위치 확인
- Android SDK와 연결 기기가 없는 환경이므로 APK 컴파일, 실기기 설치와 백그라운드 갱신은 미검증

구현 참고: [위젯 등록](https://saleksovski.github.io/react-native-android-widget/docs/tutorial/register-widget-expo), [위젯 렌더링 제약](https://saleksovski.github.io/react-native-android-widget/docs/limitations).
