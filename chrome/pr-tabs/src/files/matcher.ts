export function matchesGlob(path: string, pattern: string): boolean {
  const source = pattern.split("*").map(escapeRegex).join(".*");

  return pattern.length > 0 && new RegExp(`^${source}$`, "u").test(path);
}

function escapeRegex(value: string): string {
  return value.replaceAll(/[\\^$.*+?()[\]{}|]/g, "\\$&");
}

export function hiddenPaths(paths: readonly string[], patterns: readonly string[]): Set<string> {
  return new Set(paths.filter((path) => patterns.some((pattern) => matchesGlob(path, pattern))));
}
