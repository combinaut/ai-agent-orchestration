import type { MatchRange, Segment } from "#/types";

function mergedRanges(indices: readonly MatchRange[], length: number): MatchRange[] {
  const sorted = indices
    .map((range): MatchRange => [Math.max(0, range[0]), Math.min(length - 1, range[1])])
    .filter((range) => range[0] <= range[1])
    .toSorted((a, b) => a[0] - b[0]);
  const merged: MatchRange[] = [];

  for (const range of sorted) {
    const previous = merged.at(-1);
    if (previous && range[0] <= previous[1] + 1) {
      merged[merged.length - 1] = [previous[0], Math.max(previous[1], range[1])];
    } else {
      merged.push(range);
    }
  }

  return merged;
}

export function highlightSegments(text: string, indices: readonly MatchRange[] = []): Segment[] {
  const segments: Segment[] = [];
  let cursor = 0;

  for (const range of mergedRanges(indices, text.length)) {
    if (range[0] > cursor) {
      segments.push({ text: text.slice(cursor, range[0]), matched: false });
    }
    segments.push({ text: text.slice(range[0], range[1] + 1), matched: true });
    cursor = range[1] + 1;
  }

  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), matched: false });
  }

  return segments;
}
