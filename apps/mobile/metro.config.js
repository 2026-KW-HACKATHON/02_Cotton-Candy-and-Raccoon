const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

// Windows에서 큰 multipart 번들을 Android가 읽지 못하는 문제를 일반 JS 응답으로 우회한다.
// https://github.com/expo/expo/issues/49111
if (process.platform === "win32") {
  const enhanceMiddleware = config.server.enhanceMiddleware;
  config.server.enhanceMiddleware = (middleware, server) => {
    const enhancedMiddleware = enhanceMiddleware
      ? enhanceMiddleware(middleware, server)
      : middleware;
    return (req, res, next) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      if (
        url.pathname.endsWith(".bundle") &&
        url.searchParams.get("platform") === "android"
      ) {
        req.headers.accept = "application/javascript";
      }
      return enhancedMiddleware(req, res, next);
    };
  };
}

module.exports = config;
