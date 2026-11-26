const test = require("node:test");
const assert = require("node:assert/strict");
const logic = require("../coordinator/todo-panel.js");

const items = [{ key: "k1" }, { key: "k2" }, { key: "k3" }];
let counter = 0;
const makeId = () => "id" + ++counter;

test("visibleItems hides the items already sent", () => {
  const shown = logic.visibleItems(items, [{ id: "a", key: "k2", answer: "x" }]);
  assert.deepEqual(shown.map((item) => item.key), ["k1", "k3"]);
  const saved = JSON.stringify({
    answers: [
      { id: "a", key: "k1", answer: "now" },
      { id: "b", key: "k3", answer: "later" },
    ],
  });
  const reloaded = logic.visibleItems(items, logic.parseAnswers(saved));
  assert.deepEqual(reloaded.map((item) => item.key), ["k2"]);
});

test("collectAnswers takes only the items that have an answer", () => {
  const entries = logic.collectAnswers(items, { k1: "now", k2: "   ", k3: " later " }, makeId);
  assert.deepEqual(entries.map(({ key, answer }) => [key, answer]), [["k1", "now"], ["k3", "later"]]);
});

test("collectAnswers cuts an answer at the limit", () => {
  const [entry] = logic.collectAnswers(items, { k1: "x".repeat(600) }, makeId);
  assert.equal(entry.answer.length, logic.ANSWER_MAX);
});

test("mergePending keeps earlier entries and replaces one with the same id", () => {
  const merged = logic.mergePending(
    [{ id: "a", key: "k1", answer: "old" }, { id: "b", key: "k2", answer: "keep" }],
    [{ id: "a", key: "k1", answer: "new" }, { id: "c", key: "k3", answer: "add" }],
  );
  assert.deepEqual(merged.map((entry) => [entry.id, entry.answer]), [["a", "new"], ["b", "keep"], ["c", "add"]]);
});

test("parseAnswers survives a missing or broken file", () => {
  assert.deepEqual(logic.parseAnswers("not json"), []);
  assert.deepEqual(logic.parseAnswers("{}"), []);
  assert.deepEqual(logic.parseAnswers('{"answers":[{"id":"a","key":"k"},{"id":1}]}'), [{ id: "a", key: "k" }]);
  assert.deepEqual(logic.parseAnswers('{"answers":{"id":"a","key":"k"}}'), []);
  const mixed = '{"answers":[null,{"id":1,"key":"k"},{"id":"a","key":2},{"id":"b","key":"k"}]}';
  assert.deepEqual(logic.parseAnswers(mixed), [{ id: "b", key: "k" }]);
});

test("classifyError maps each code to one handling", () => {
  assert.equal(logic.classifyError("conflict"), "reload");
  for (const code of ["not_writer", "not_granted", "capability_disabled", "capability_removed", "consent_required", "not_declared"]) {
    assert.equal(logic.classifyError(code), "read-only");
  }
  for (const code of ["rate_limited", "too_large", "upstream_error", undefined]) {
    assert.equal(logic.classifyError(code), "failed");
  }
});

test("wakePlan sends only when the viewer can send to Claude", () => {
  const plan = logic.wakePlan("available", 2);
  assert.equal(plan.send, true);
  assert.equal(plan.text, "2 answers submitted on the to-do panel. Run /todo-panel.");
  assert.equal(logic.wakeText(1), "1 answer submitted on the to-do panel. Run /todo-panel.");
  for (const state of ["writers_only", "no_session", "off", undefined]) {
    const saved = logic.wakePlan(state, 2);
    assert.equal(saved.send, false);
    assert.equal(saved.text, "Saved. Claude applies them at the next /todo-panel run.");
  }
});

test("wakeFailure stops the wake only on a permanent code", () => {
  for (const code of ["forbidden", "consent_required", "not_granted", "capability_disabled", "capability_removed"]) {
    const result = logic.wakeFailure(code);
    assert.equal(result.stop, true);
    assert.equal(result.message, logic.WAKE_OFF_MESSAGE);
  }
  for (const code of ["claude_unavailable", "rate_limited", "unavailable", undefined]) {
    const result = logic.wakeFailure(code);
    assert.equal(result.stop, false);
    assert.equal(result.message, logic.SAVED_MESSAGE);
  }
});
