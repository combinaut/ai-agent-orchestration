import type { FuseResult } from "fuse.js";
import type { MatchRange, PrRow, SearchPr, Segment } from "#/types";

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

export function highlightSegments(
  text: string,
  indices: readonly MatchRange[],
  queryLength: number,
): Segment[] {
  if (queryLength === 0 || indices.length === 0) {
    return text ? [{ text, matched: false }] : [];
  }

  const minimum = queryLength === 1 ? 1 : 2;
  const ranges = mergedRanges(indices, text.length).filter(
    (range) => range[1] - range[0] + 1 >= minimum,
  );
  const segments: Segment[] = [];
  let cursor = 0;

  for (const range of ranges) {
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

export function highlightedRow(result: FuseResult<SearchPr>, queryLength: number): PrRow {
  const matches = result.matches ?? [];
  const label = matches.find((match) => match.key === "label")?.indices ?? [];
  const title = matches.find((match) => match.key === "title")?.indices ?? [];

  return { ...result.item.row, highlights: { label, title, queryLength } };
}

export function rowSegments(row: PrRow): Segment[] {
  const highlights = row.highlights;
  const offset = row.label.length + 1;
  const title =
    highlights?.title.map((range): MatchRange => [range[0] + offset, range[1] + offset]) ?? [];
  const indices = [...(highlights?.label ?? []), ...title];

  return highlightSegments(
    `${row.label} ${row.title}`.trim(),
    indices,
    highlights?.queryLength ?? 0,
  );
}
