import assert from "node:assert/strict";
import { test } from "vitest";
import { highlightSegments } from "#/highlight";

test("highlight ranges merge overlaps and adjacency", () => {
  assert.deepEqual(
    highlightSegments("abcdefgh", [
      [5, 5],
      [2, 3],
      [3, 4],
    ]),
    [
      { text: "ab", matched: false },
      { text: "cdef", matched: true },
      { text: "gh", matched: false },
    ],
  );
});

test("only matched characters are marked; no ranges mark nothing", () => {
  assert.deepEqual(
    highlightSegments("abcdef", [
      [1, 2],
      [4, 5],
    ]),
    [
      { text: "a", matched: false },
      { text: "bc", matched: true },
      { text: "d", matched: false },
      { text: "ef", matched: true },
    ],
  );
  assert.deepEqual(highlightSegments("abcdef"), [{ text: "abcdef", matched: false }]);
  assert.deepEqual(highlightSegments("abc", [[1, 9]]), [
    { text: "a", matched: false },
    { text: "bc", matched: true },
  ]);
  assert.deepEqual(highlightSegments(""), []);
});
