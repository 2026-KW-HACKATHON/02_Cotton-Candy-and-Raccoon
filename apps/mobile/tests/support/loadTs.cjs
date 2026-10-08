const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
function loadTs(file, overrides = {}) {
  const cache = new Map();
  function load(filename) {
    filename = path.resolve(filename);
    if (cache.has(filename)) return cache.get(filename).exports;
    const module = { exports: {} };
    cache.set(filename, module);
    const source = fs.readFileSync(filename, "utf8");
    const compiled = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText;
    new Function("module", "exports", "require", compiled)(
      module,
      module.exports,
      (name) => {
        if (name in overrides) return overrides[name];
        if (name.startsWith("."))
          return load(path.resolve(path.dirname(filename), name) + ".ts");
        return require(name);
      },
    );
    return module.exports;
  }
  return load(file);
}
module.exports = { loadTs };
