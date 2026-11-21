"use strict";

const ANSWER_MAX = 512;

function pendingKeys(pending) {
  return new Set(pending.map((entry) => entry.key));
}

function visibleItems(items, pending) {
  const sent = pendingKeys(pending);
  return items.filter((item) => !sent.has(item.key));
}

function collectAnswers(items, values, makeId) {
  const entries = [];
  for (const item of items) {
    const answer = (values[item.key] || "").trim();
    if (answer) entries.push({ id: makeId(), key: item.key, answer: answer.slice(0, ANSWER_MAX) });
  }
  return entries;
}

function mergePending(pending, entries) {
  const merged = new Map(pending.map((entry) => [entry.id, entry]));
  for (const entry of entries) merged.set(entry.id, entry);
  return [...merged.values()];
}

function parseAnswers(text) {
  try {
    const list = JSON.parse(text).answers;
    return Array.isArray(list)
      ? list.filter((entry) => entry && typeof entry.id === "string" && typeof entry.key === "string")
      : [];
  } catch {
    return [];
  }
}

const READ_ONLY_CODES = new Set([
  "not_writer",
  "not_granted",
  "capability_disabled",
  "capability_removed",
  "consent_required",
  "not_declared",
]);

function classifyError(code) {
  if (code === "conflict") return "reload";
  if (READ_ONLY_CODES.has(code)) return "read-only";
  return "failed";
}

const SAVED_MESSAGE = "Saved. Claude applies them at the next /todo-panel run.";

function wakeText(count) {
  return count + (count === 1 ? " answer" : " answers") + " submitted on the to-do panel. Run /todo-panel.";
}

function wakePlan(canSend, count) {
  return canSend === "available" ? { send: true, text: wakeText(count) } : { send: false, text: SAVED_MESSAGE };
}

const WAKE_OFF_MESSAGE = "Saved. Sending to Claude is off for this page; Claude applies them at the next /todo-panel run.";

const PERMANENT_WAKE_CODES = new Set(["forbidden", "consent_required", "not_granted", "capability_disabled", "capability_removed"]);

function wakeFailure(code) {
  return PERMANENT_WAKE_CODES.has(code)
    ? { stop: true, message: WAKE_OFF_MESSAGE }
    : { stop: false, message: SAVED_MESSAGE };
}

if (typeof module !== "undefined") {
  module.exports = { visibleItems, collectAnswers, mergePending, parseAnswers, classifyError, wakePlan, wakeText, wakeFailure, WAKE_OFF_MESSAGE, SAVED_MESSAGE, ANSWER_MAX };
}
