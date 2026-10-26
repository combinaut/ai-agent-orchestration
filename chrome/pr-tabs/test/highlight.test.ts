import assert from "node:assert/strict";
import { test } from "vitest";
import { highlightSegments } from "#/highlight";

test("highlight ranges merge overlaps and adjacency before filtering short matches", () => {
  assert.deepEqual(
    highlightSegments(
      "abcdefgh",
      [
        [5, 5],
        [2, 3],
        [3, 4],
      ],
      3,
    ),
    [
      { text: "ab", matched: false },
      { text: "cdef", matched: true },
      { text: "gh", matched: false },
    ],
  );
});

test("only matched characters are marked and an empty query never marks", () => {
  assert.deepEqual(
    highlightSegments(
      "abcdef",
      [
        [1, 2],
        [4, 5],
      ],
      2,
    ),
    [
      { text: "a", matched: false },
      { text: "bc", matched: true },
      { text: "d", matched: false },
      { text: "ef", matched: true },
    ],
  );
  assert.deepEqual(highlightSegments("abcdef", [[1, 2]], 0), [{ text: "abcdef", matched: false }]);
  assert.deepEqual(highlightSegments("abcdef", [[1, 1]], 2), [{ text: "abcdef", matched: false }]);
  assert.deepEqual(highlightSegments("abc", [[1, 1]], 1), [
    { text: "a", matched: false },
    { text: "b", matched: true },
    { text: "c", matched: false },
  ]);
});
