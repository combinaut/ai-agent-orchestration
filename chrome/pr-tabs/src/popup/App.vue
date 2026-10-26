<script setup lang="ts">
import { computed, onMounted, ref, useTemplateRef } from "vue";
import type { GroupListControl, PrKey, TabId } from "#/types";
import { useFuzzyFilter } from "#/composables/useFuzzyFilter";
import { usePrClose } from "#/composables/usePrClose";
import PrSearch from "./components/PrSearch.vue";
import { useOpeningProgress } from "#/composables/useOpeningProgress";
import { useMessages } from "#/composables/useMessages";
import { usePanes } from "#/composables/usePanes";
import { useGoTo } from "#/composables/useGoTo";
import { useRepoPicker } from "#/composables/useRepoPicker";
import { useServiceWorker } from "#/composables/useServiceWorker";
import { useStatus } from "#/composables/useStatus";
import { useSummary } from "#/composables/useSummary";
import { useTabActions } from "#/composables/useTabActions";
import ActionButton from "./components/ActionButton.vue";
import GoToForm from "./components/GoToForm.vue";
import GroupList from "./components/GroupList.vue";
import OpeningProgress from "./components/OpeningProgress.vue";
import StatusLine from "./components/StatusLine.vue";

const {
  openReviewsButton,
  openReviewsHint,
  openAllButton,
  openAllHint,
  checkNowButton,
  checkNowHint,
  closeDuplicatesButton,
  closeDuplicatesHint,
} = useMessages([
  "openReviewsButton",
  "openReviewsHint",
  "openAllButton",
  "openAllHint",
  "checkNowButton",
  "checkNowHint",
  "closeDuplicatesButton",
  "closeDuplicatesHint",
]);
const { ask } = useServiceWorker();
const status = useStatus();
const { statusText, isStatusError } = status;
const { groups, refresh } = useSummary(ask, status);
const panes = usePanes(ask, status);
const query = ref("");
const {
  groups: visibleGroups,
  active: isFiltering,
  count: matchCount,
  best: bestMatch,
} = useFuzzyFilter(groups, query);
const groupList = useTemplateRef<GroupListControl>("groupList");
const closing = usePrClose({ ask, status, refresh });
const input = ref("");
const picker = useRepoPicker(ask, input);
const { repos, repo, visible: isRepoPickerVisible } = picker;
const { busy: isGoToBusy, go, focusPr } = useGoTo({ ask, status, picker }, input);
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

function handleSearchNext(): void {
  groupList.value?.focusFirst();
}

function handleSearchGo(): void {
  if (bestMatch.value) {
    void focusPr(bestMatch.value);
  }
}

function handlePrClose(key: PrKey, tabId: TabId): void {
  void closing.close(key, tabId);
}

function handlePrGo(key: PrKey): void {
  void focusPr(key);
}

function handleGoToSubmit(): void {
  void go();
}

function handleRepoPick(): void {
  picker.pick();
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
}

onMounted(handlePopupMounted);
</script>

<template>
  <main
    id="popup-content"
    class="box-border max-h-popup-content w-popup scrollbar-gutter-both-edges overflow-y-auto p-popup-padding"
  >
    <GoToForm
      v-model:input="input"
      v-model:repo="repo"
      :busy="isGoToBusy"
      :repos="repos"
      :show-repo="isRepoPickerVisible"
      @go="handleGoToSubmit"
      @pick="handleRepoPick"
    />
    <div class="mt-3 grid grid-cols-2 gap-2">
      <ActionButton
        id="open-all"
        primary
        :label="openAllButton"
        :hint="openAllHint"
        :busy="isOpeningBatch"
        @run="handleOpenAllRun"
      />
      <ActionButton
        id="open-reviews"
        :label="openReviewsButton"
        :hint="openReviewsHint"
        :busy="isOpeningBatch"
        @run="handleOpenReviewsRun"
      />
      <ActionButton
        id="check-now"
        :label="checkNowButton"
        :hint="checkNowHint"
        :busy="isCheckingNow"
        @run="handleCheckNowRun"
      />
      <ActionButton
        id="close-duplicates"
        :label="closeDuplicatesButton"
        :hint="closeDuplicatesHint"
        :busy="isClosingDuplicates"
        @run="handleCloseDuplicatesRun"
      />
    </div>
    <OpeningProgress :progress="openingProgress" />
    <StatusLine :text="statusText" :error="isStatusError" />
    <PrSearch
      v-model="query"
      :active="isFiltering"
      :count="matchCount"
      @next="handleSearchNext"
      @go="handleSearchGo"
    />
    <GroupList
      ref="groupList"
      :groups="visibleGroups"
      :panes="panes"
      :filtering="isFiltering"
      @go="handlePrGo"
      @close="handlePrClose"
    />
  </main>
</template>
