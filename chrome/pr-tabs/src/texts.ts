// Popup status lines built from catalog messages.
import { getMessage, getPluralMessage, listText } from "#/i18n";
import type { CheckResult, OpenAllResult, Summary } from "#/types";

export function duplicateText(closed: number | undefined): string {
  if (!closed) {
    return getMessage("noDuplicates");
  }

  return getPluralMessage("closedDuplicates", closed);
}

export function summaryText(summary: Summary): string {
  if (!summary.prs) {
    return getMessage("noPrTabs");
  }

  return getMessage("summaryLine", {
    prs: getPluralMessage("prCount", summary.prs),
    groups: getPluralMessage("groupCount", summary.groups.length),
  });
}

function closedParts(closed: CheckResult["closed"]): string[] {
  const merged = closed.filter((one) => one.state === "MERGED").length;
  const unmerged = closed.length - merged;
  const parts: string[] = [];

  if (merged) {
    parts.push(getPluralMessage("mergedTabs", merged));
  }

  if (unmerged) {
    parts.push(getPluralMessage("closedPrTabs", unmerged));
  }

  return parts;
}

function closedClause(result: CheckResult): string {
  const parts = closedParts(result.closed);

  if (!parts.length) {
    return getPluralMessage("noneClosed", result.checked);
  }

  return getMessage("closedTabs", { tabs: listText(parts) });
}

function checkClauses(result: CheckResult): string[] {
  const { prs, checked, inView } = result;
  const clauses = [closedClause(result)];

  if (inView.length) {
    clauses.push(getPluralMessage("inViewKept", inView.length, { prs: inView.join(", ") }));
  }

  if (checked < prs) {
    clauses.push(getMessage("moreNextTime", { count: prs - checked }));
  }

  return clauses;
}

// The popup line for a check result.
export function checkText(result: CheckResult): string {
  if (!result.tabs) {
    return getMessage("noPrTabs");
  }

  return `${checkClauses(result).join("; ")}.`;
}

export function openAllText(result: OpenAllResult, summary: string): string {
  const { opened, remaining, estimated } = result;
  const done = getMessage("openedCount", { count: opened });

  if (!remaining) {
    return `${done} ${summary}`;
  }

  const more = estimated ? "aboutMoreNotOpened" : "moreNotOpened";

  return `${done} ${getMessage(more, { count: remaining })}`;
}
