import { checkText, duplicateText, summaryText } from "./core.js";
import { ask as askWith } from "./messages.js";

const COLOURS = new Set(["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"]);
const status = document.getElementById("status");
const target = document.getElementById("target");
const repo = document.getElementById("repo");
const openAll = document.getElementById("open-all");
const groups = document.getElementById("groups");
const checkNow = document.getElementById("check-now");
const closeDuplicates = document.getElementById("close-duplicates");

const ask = (message) => askWith((m) => chrome.runtime.sendMessage(m), message);

function show(text, isError = false) {
  status.textContent = text;
  status.classList.toggle("error", isError);
}

async function refresh() {
  const summary = await ask({ type: "summary" });
  if (summary.error) {
    show(summary.error, true);
    return;
  }
  show(summaryText(summary));
  groups.replaceChildren(
    ...summary.groups.map(({ title, color, count }) => {
      const item = document.createElement("li");
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.setAttribute("aria-hidden", "true");
      if (COLOURS.has(color)) dot.style.background = `var(--${color})`;
      const name = document.createElement("span");
      name.textContent = title;
      const total = document.createElement("span");
      total.className = "count";
      total.textContent = String(count);
      total.setAttribute("aria-label", `${count} PR${count === 1 ? "" : "s"}`);
      item.append(dot, name, total);
      return item;
    }),
  );
}

async function loadRepos() {
  const choices = await ask({ type: "repos" });
  if (choices.error || !choices.show) return;
  repo.replaceChildren(
    ...choices.repos.map((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      return option;
    }),
  );
  repo.value = choices.selected;
  repo.hidden = false;
}

target.focus();
refresh();
loadRepos();

document.getElementById("go").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!target.value.trim() || target.readOnly) return;
  target.readOnly = true;
  const result = await ask({ type: "goTo", input: target.value, ...(!repo.hidden && { repo: repo.value }) });
  target.readOnly = false;
  if (result.error) {
    show(result.error, true);
    return;
  }
  window.close();
});

openAll.addEventListener("click", async () => {
  openAll.disabled = true;
  show("Opening…");
  const result = await ask({ type: "openAll" });
  openAll.disabled = false;
  if (result.error) {
    show(result.error, true);
    return;
  }
  await refresh();
  const opened = `Opened ${result.opened}.`;
  const more = `${result.estimated ? "about " : ""}${result.remaining} more`;
  show(result.remaining ? `${opened} ${more} not opened; click again for the next batch.` : `${opened} ${status.textContent}`);
});

checkNow.addEventListener("click", async () => {
  checkNow.disabled = true;
  show("Checking…");
  const result = await ask({ type: "checkNow" });
  checkNow.disabled = false;
  if (result.error) {
    show(result.error, true);
    return;
  }
  await refresh();
  show(checkText(result));
});

closeDuplicates.addEventListener("click", async () => {
  closeDuplicates.disabled = true;
  show("Closing…");
  const result = await ask({ type: "closeDuplicates" });
  closeDuplicates.disabled = false;
  if (result.error) {
    show(result.error, true);
    return;
  }
  await refresh();
  show(duplicateText(result.closed));
});
