// @vitest-environment happy-dom
import assert from "node:assert/strict";
import Badge from "primevue/badge";
import AccordionContent from "primevue/accordioncontent";
import AccordionHeader from "primevue/accordionheader";
import { test } from "vitest";
import GroupList from "#popup/components/GroupList.vue";
import type { GroupSummary, PrRow } from "#/types";
import { fakePanes } from "./fakePanes";
import { mountPopup } from "./mountPopup";

interface AccordionContentPassThrough {
  readonly content: {
    readonly class: string;
  };
}

interface AccordionHeaderPassThrough {
  readonly root: {
    readonly class: string;
  };
}

function hasContentClass(value: unknown): value is AccordionContentPassThrough {
  if (typeof value !== "object" || value === null || !("content" in value)) {
    return false;
  }

  const { content } = value;

  return (
    typeof content === "object" &&
    content !== null &&
    "class" in content &&
    typeof content.class === "string"
  );
}

function hasHeaderClass(value: unknown): value is AccordionHeaderPassThrough {
  if (typeof value !== "object" || value === null || !("root" in value)) {
    return false;
  }

  const { root } = value;

  return (
    typeof root === "object" && root !== null && "class" in root && typeof root.class === "string"
  );
}

test("a row shows the group's colour dot, title and PR count", () => {
  const groups: GroupSummary[] = [{ title: "Finder", color: "yellow", count: 2, prs: [] }];
  const wrapper = mountPopup(GroupList, { props: { groups, panes: fakePanes() } });

  assert.ok(wrapper.find("[aria-hidden=true]").classes().includes("bg-group-yellow"));
  assert.match(wrapper.text(), /Finder/);

  const badge = wrapper.findComponent(Badge);
  const passThrough = wrapper.findComponent(AccordionHeader).props("pt");

  assert.equal(badge.props("value"), 2);
  assert.equal(badge.attributes("aria-label"), "2 PRs");
  assert.ok(hasHeaderClass(passThrough));
  assert.equal(passThrough.root.class, "-mx-popup-padding px-popup-padding");
});

test("the expanded content is a shrinkable grid column, so a long PR title cannot widen the popup", async () => {
  const prs: PrRow[] = [
    { key: "acme/webapp#1", label: "webapp#1", title: "T".repeat(200), head: null },
  ];
  const groups: GroupSummary[] = [{ title: "Finder", count: 1, prs }];
  const wrapper = mountPopup(GroupList, { props: { groups, panes: fakePanes() } });

  await wrapper.find("button[aria-expanded]").trigger("click");

  assert.ok(wrapper.find("[data-pc-name=accordioncontent]").classes().includes("grid-cols-1"));
  const passThrough = wrapper.findComponent(AccordionContent).props("pt");

  assert.ok(hasContentClass(passThrough));
  assert.equal(passThrough.content.class, "p-0");
});
