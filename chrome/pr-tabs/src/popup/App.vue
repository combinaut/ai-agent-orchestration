<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useGoTo } from "../composables/useGoTo";
import { useRepoPicker } from "../composables/useRepoPicker";
import { useServiceWorker } from "../composables/useServiceWorker";
import { useStatus } from "../composables/useStatus";
import { useSummary } from "../composables/useSummary";
import { useTabActions } from "../composables/useTabActions";
import ActionButton from "./components/ActionButton.vue";
import GoToForm from "./components/GoToForm.vue";
import GroupList from "./components/GroupList.vue";
import StatusLine from "./components/StatusLine.vue";

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

function handleGoToSubmit() {
  void go();
}

function handleRepoPick() {
  picker.pick();
}

function handleOpenAllRun() {
  void openAll.run();
}

function handleCheckNowRun() {
  void checkNow.run();
}

function handleCloseDuplicatesRun() {
  void closeDuplicates.run();
}

onMounted(() => {
  void refresh();
  void picker.load();
});
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
    label="Open all my open PRs"
    :busy="isOpeningAll"
    @run="handleOpenAllRun"
  />
  <ActionButton
    id="check-now"
    label="Close merged PRs"
    :busy="isCheckingNow"
    @run="handleCheckNowRun"
  />
  <ActionButton
    id="close-duplicates"
    label="Close duplicate tabs"
    :busy="isClosingDuplicates"
    @run="handleCloseDuplicatesRun"
  />
  <StatusLine :text="statusText" :error="isStatusError" />
  <GroupList :groups="groups" />
</template>
