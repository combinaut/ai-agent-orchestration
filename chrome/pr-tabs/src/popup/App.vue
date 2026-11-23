<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { bareNumber, isGoToInput } from "#/core";
import { getMessage } from "#/i18n";
import type { ActionMenuItem, PrKey, RepoOption, SearchQuery, Selectable, TabId } from "#/types";
import { useCollapsed } from "#/composables/useCollapsed";
import { usePrClose } from "#/composables/usePrClose";
import { useOpeningProgress } from "#/composables/useOpeningProgress";
import { useMessages } from "#/composables/useMessages";
import { usePanes } from "#/composables/usePanes";
import { useGoTo } from "#/composables/useGoTo";
import { useRepoOptions } from "#/composables/useRepoOptions";
import { useRepoPicker } from "#/composables/useRepoPicker";
import { useSearchFilter } from "#/composables/useSearchFilter";
import { useServiceWorker } from "#/composables/useServiceWorker";
import { useStatus } from "#/composables/useStatus";
import { useSummary } from "#/composables/useSummary";
import { useTabActions } from "#/composables/useTabActions";
import ActionMenu from "./components/ActionMenu.vue";
import GroupList from "./components/GroupList.vue";
import OpeningProgress from "./components/OpeningProgress.vue";
import PopupField from "./components/PopupField.vue";
import RepoOptionItem from "./components/RepoOptionItem.vue";
import StatusToast from "./components/StatusToast.vue";

const {
  goToLabel,
  openReviewsButton,
  openAllButton,
  checkNowButton,
  closeDuplicatesButton,
  actionMenuLabel,
  repoSelectLabel,
  repoGuessUnavailable,
  noPrTabs,
  emptyHint,
  footerListHint,
  footerGoToHint,
} = useMessages([
  "goToLabel",
  "openReviewsButton",
  "openAllButton",
  "checkNowButton",
  "closeDuplicatesButton",
  "actionMenuLabel",
  "repoSelectLabel",
  "repoGuessUnavailable",
  "noPrTabs",
  "emptyHint",
  "footerListHint",
  "footerGoToHint",
]);
const { ask } = useServiceWorker();
const status = useStatus();
const { statusText, statusKind, isStatusError, revision: statusRevision } = status;
const { groups, text: summaryLine, refresh } = useSummary(ask, status);
const isStatusShown = computed(
  () => isStatusError.value || (statusText.value !== "" && statusText.value !== summaryLine.value),
);
const hasPrTabs = computed(() => groups.value.length > 0);
const panes = usePanes(ask, status);
const input = ref("");
const mode = computed<"idle" | "goto" | "search">(() => {
  const value = input.value.trim();

  if (!value) {
    return "idle";
  }

  return isGoToInput(value) ? "goto" : "search";
});
const number = computed(() => (mode.value === "goto" ? bareNumber(input.value) : null));
const query = computed<SearchQuery>(() => {
  if (mode.value === "search") {
    return { text: input.value, numberOnly: false };
  }

  return { text: number.value === null ? "" : String(number.value), numberOnly: true };
});
const {
  groups: visibleGroups,
  active: isFiltering,
  count: matchCount,
} = useSearchFilter(groups, query);
const closing = usePrClose({ ask, status, refresh });
const picker = useRepoPicker(ask, input);
const { repos, repo, guessUnavailable } = picker;
const isRepoPickerOpen = ref(false);
const repoOptions = useRepoOptions({ groups, repos, guessed: repo, number });
const shownRepoOptions = computed(() =>
  isRepoPickerOpen.value && mode.value === "goto" ? repoOptions.value : [],
);
const { busy: isGoToBusy, go, focusPr } = useGoTo({ ask, status, picker, refresh }, input);
const { openAll, openReviews, checkNow, closeDuplicates } = useTabActions({ ask, status, refresh });
const { busy: isOpeningAll } = openAll;
const { busy: isOpeningReviews } = openReviews;
const { busy: isCheckingNow } = checkNow;
const { busy: isClosingDuplicates } = closeDuplicates;
const localBusy = computed(() => isOpeningAll.value || isOpeningReviews.value);
const workerProgress = useOpeningProgress({ ask, status, refresh, localBusy });
const openingProgress = computed(() => ({
  ...workerProgress.value,
  active: workerProgress.value.active || localBusy.value,
}));
const isOpeningBatch = computed(() => openingProgress.value.active);
const { collapsed: storedCollapsed, load: loadCollapsed, set: setStoredCollapsed } = useCollapsed();
const searchCollapsed = ref<ReadonlySet<string>>(new Set());
const collapsed = computed(() =>
  isFiltering.value ? searchCollapsed.value : storedCollapsed.value,
);
const selectables = computed<Selectable[]>(() => [
  ...shownRepoOptions.value.map((option) => ({ kind: "repo" as const, id: option.id, option })),
  ...visibleGroups.value
    .filter((group) => !collapsed.value.has(group.id))
    .flatMap((group) =>
      group.prs.map((row) => ({ kind: "pr" as const, id: `pr-${row.tabId}`, row })),
    ),
]);
const activeIndex = ref(-1);
const active = computed(() => selectables.value[activeIndex.value] ?? null);
const activeId = computed(() => active.value?.id ?? null);
const totalPrs = computed(() => groups.value.reduce((total, group) => total + group.prs.length, 0));
const footerSummary = computed(() =>
  isFiltering.value
    ? getMessage("footerFiltered", { matches: matchCount.value, total: totalPrs.value })
    : summaryLine.value,
);
const footerHint = computed(() => (mode.value === "goto" ? footerGoToHint : footerListHint));
const openInLabel = computed(() => getMessage("goToOpenIn", { number: number.value ?? "" }));
const matchingLabel = computed(() => getMessage("goToMatching", { number: number.value ?? "" }));
const hasRepoOptions = computed(() => shownRepoOptions.value.length > 0);
const repoOptionRows = computed<Record<string, RepoOption>>(() =>
  Object.fromEntries(shownRepoOptions.value.map((option) => [option.id, option])),
);
const isMatchingLabelShown = computed(() => hasRepoOptions.value && visibleGroups.value.length > 0);
const isListShown = computed(() => hasPrTabs.value || hasRepoOptions.value);

const openingMenuItems = computed<ActionMenuItem[]>(() => [
  {
    label: openAllButton,
    icon: "pi-external-link",
    busy: isOpeningBatch.value,
    run: handleOpenAllRun,
  },
  {
    label: openReviewsButton,
    icon: "pi-eye",
    busy: isOpeningBatch.value,
    run: handleOpenReviewsRun,
  },
]);
const closingMenuItems = computed<ActionMenuItem[]>(() => [
  {
    label: checkNowButton,
    icon: "pi-check-circle",
    busy: isCheckingNow.value || !hasPrTabs.value,
    separated: true,
    run: handleCheckNowRun,
  },
  {
    label: closeDuplicatesButton,
    icon: "pi-clone",
    busy: isClosingDuplicates.value || !hasPrTabs.value,
    run: handleCloseDuplicatesRun,
  },
]);
const actionMenuItems = computed(() => [...openingMenuItems.value, ...closingMenuItems.value]);

function resetSelection(): void {
  const selectsFirst = mode.value === "search" || shownRepoOptions.value.length > 0;

  activeIndex.value = selectsFirst && selectables.value.length > 0 ? 0 : -1;
}

function handleQueryChanged(): void {
  searchCollapsed.value = new Set();
  resetSelection();
}

function handleSelectablesChanged(): void {
  if (activeIndex.value >= selectables.value.length) {
    activeIndex.value = selectables.value.length - 1;
  }
}

function handleActiveChanged(id: string | null): void {
  if (id) {
    void nextTick(() => document.getElementById(id)?.scrollIntoView({ block: "nearest" }));
  }
}

function handleSelectNext(): void {
  activeIndex.value = Math.min(activeIndex.value + 1, selectables.value.length - 1);
}

function handleSelectPrevious(): void {
  activeIndex.value = Math.max(activeIndex.value - 1, 0);
}

function handleCollapse(id: string, isCollapsed: boolean): void {
  if (isFiltering.value) {
    const next = new Set(searchCollapsed.value);

    if (isCollapsed) {
      next.add(id);
    } else {
      next.delete(id);
    }

    searchCollapsed.value = next;
  } else {
    setStoredCollapsed(id, isCollapsed);
  }
}

function handlePrClose(key: PrKey, tabId: TabId): void {
  void closing.close(key, tabId);
}

function handlePrGo(key: PrKey): void {
  void focusPr(key);
}

function openRepo(option: RepoOption): void {
  if (option.open) {
    void focusPr(option.open);

    return;
  }

  repo.value = option.repo;
  picker.pick();
  void go();
}

function handleRepoPick(option: RepoOption): void {
  openRepo(option);
}

function handleRepoHover(option: RepoOption): void {
  activeIndex.value = selectables.value.findIndex((item) => item.id === option.id);
}

function handlePrHover(tabId: TabId): void {
  activeIndex.value = selectables.value.findIndex((item) => item.id === `pr-${tabId}`);
}

function openSelected(selected: Selectable): void {
  if (selected.kind === "repo") {
    openRepo(selected.option);
  } else {
    void focusPr(selected.row.key);
  }
}

async function hasRepoChoice(): Promise<boolean> {
  await picker.ready();

  return repos.value.length > 0;
}

async function handleFieldGo(): Promise<void> {
  if (active.value) {
    openSelected(active.value);
  } else if (number.value !== null && (await hasRepoChoice())) {
    picker.suggest();
    isRepoPickerOpen.value = true;
  } else if (mode.value === "goto") {
    void go();
  }
}

function handleFieldClear(): void {
  if (isRepoPickerOpen.value) {
    isRepoPickerOpen.value = false;

    return;
  }

  input.value = "";
}

function handleInputChanged(): void {
  isRepoPickerOpen.value = false;
}

function handleOpenAllRun(): void {
  void openAll.run();
}

function handleOpenReviewsRun(): void {
  void openReviews.run();
}

function handleCheckNowRun(): void {
  void checkNow.run();
}

function handleCloseDuplicatesRun(): void {
  void closeDuplicates.run();
}

function handlePopupMounted(): void {
  void refresh();
  void picker.load();
  void loadCollapsed();
}

watch(input, handleInputChanged);
watch([() => query.value.text, isRepoPickerOpen, mode], handleQueryChanged);
watch(selectables, handleSelectablesChanged);
watch(activeId, handleActiveChanged);
onMounted(handlePopupMounted);
</script>

<template>
  <main id="popup-content">
    <header class="popup-title">
      <h1>{{ goToLabel }}</h1>
      <ActionMenu :actions="actionMenuItems" :label="actionMenuLabel" />
    </header>
    <PopupField
      v-model="input"
      :mode="mode"
      :matches="matchCount"
      :busy="isGoToBusy"
      :expanded="isListShown"
      :active="activeId"
      @go="handleFieldGo"
      @next="handleSelectNext"
      @previous="handleSelectPrevious"
      @clear="handleFieldClear"
    />
    <OpeningProgress :progress="openingProgress" />
    <section v-if="isListShown" id="popup-list" role="listbox">
      <div v-if="hasRepoOptions" role="group" :aria-label="repoSelectLabel">
        <p class="goto-label">{{ openInLabel }}</p>
        <div class="repo-options">
          <RepoOptionItem
            v-for="(option, id) in repoOptionRows"
            :key="id"
            :option="option"
            :selected="activeId"
            @pick="handleRepoPick"
            @hover="handleRepoHover"
          />
        </div>
        <p v-if="guessUnavailable" class="goto-label">{{ repoGuessUnavailable }}</p>
      </div>
      <p v-if="isMatchingLabelShown" class="goto-label goto-matching">{{ matchingLabel }}</p>
      <GroupList
        :groups="visibleGroups"
        :panes="panes"
        :collapsed="collapsed"
        :selected="activeId"
        @collapse="handleCollapse"
        @go="handlePrGo"
        @close="handlePrClose"
        @hover="handlePrHover"
      />
    </section>
    <section v-else class="empty-state">
      <p class="empty-title">{{ noPrTabs }}</p>
      <p class="empty-hint">{{ emptyHint }}</p>
    </section>
    <StatusToast
      :text="statusText"
      :kind="statusKind"
      :shown="isStatusShown"
      :revision="statusRevision"
      :raised="hasPrTabs"
    />
    <footer v-if="hasPrTabs" class="popup-footer">
      <span>{{ footerSummary }}</span>
      <span>{{ footerHint }}</span>
    </footer>
  </main>
</template>
