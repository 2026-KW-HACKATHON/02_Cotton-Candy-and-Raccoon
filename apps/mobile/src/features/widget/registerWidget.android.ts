import { TurboModuleRegistry } from "react-native";

// Expo Go has no widget native module; a development/release build is required.
if (TurboModuleRegistry.get("AndroidWidget")) {
  // Keep synchronous headless registration, without loading native code in Expo Go.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { registerWidgetTaskHandler } = require("react-native-android-widget");
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { widgetTaskHandler } = require("./widgetTaskHandler");
  registerWidgetTaskHandler(widgetTaskHandler);
}
