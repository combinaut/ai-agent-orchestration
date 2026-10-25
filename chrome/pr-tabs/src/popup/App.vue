<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useMessages } from "#/composables/useMessages";
import { useGoTo } from "#/composables/useGoTo";
import { useRepoPicker } from "#/composables/useRepoPicker";
import { useServiceWorker } from "#/composables/useServiceWorker";
import { useStatus } from "#/composables/useStatus";
import { useSummary } from "#/composables/useSummary";
import { useTabActions } from "#/composables/useTabActions";
import ActionButton from "./components/ActionButton.vue";
import GoToForm from "./components/GoToForm.vue";
import GroupList from "./components/GroupList.vue";
import StatusLine from "./components/StatusLine.vue";

const { openAllButton, checkNowButton, closeDuplicatesButton } = useMessages([
  "openAllButton",
  "checkNowButton",
  "closeDuplicatesButton",
]);
const { ask } = useServiceWorker();
const status = useStatus();
const { statusText, isStatusError } = status;
const { groups, refresh } = useSummary(ask, status);
const input = ref("");
const picker = useRepoPicker(ask, input);
const { repos, repo, visible: isRepoPickerVisible } = picker;
const { busy: isGoToBusy, go } = useGoTo({ ask, status, picker }, input);
const { openAll, checkNow, closeDuplicates } = useTabActions({ ask, status, refresh });
const { busy: isOpeningAll } = openAll;
const { busy: isCheckingNow } = checkNow;
const { busy: isClosingDuplicates } = closeDuplicates;

function handleGoToSubmit(): void {
  void go();
}

function handleRepoPick(): void {
  picker.pick();
}

function handleOpenAllRun(): void {
  void openAll.run();
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
  <GoToForm
    v-model:input="input"
    v-model:repo="repo"
    :busy="isGoToBusy"
    :repos="repos"
    :show-repo="isRepoPickerVisible"
    @go="handleGoToSubmit"
    @pick="handleRepoPick"
  />
  <ActionButton
    id="open-all"
    primary
    :label="openAllButton"
    :busy="isOpeningAll"
    @run="handleOpenAllRun"
  />
  <ActionButton
    id="check-now"
    :label="checkNowButton"
    :busy="isCheckingNow"
    @run="handleCheckNowRun"
  />
  <ActionButton
    id="close-duplicates"
    :label="closeDuplicatesButton"
    :busy="isClosingDuplicates"
    @run="handleCloseDuplicatesRun"
  />
  <StatusLine :text="statusText" :error="isStatusError" />
  <GroupList :groups="groups" />
</template>
