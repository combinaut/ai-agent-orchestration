import { createFileFilter } from "#/files/controller";
import { observeFiles } from "#/files/observe";
import { loadFileSettings, watchFileSettings } from "#/files/settings";

const filter = createFileFilter(document, () => location.href);
const stopObserving = observeFiles(document, filter.update);
let changed = false;
const stopSettings = watchFileSettings((settings) => {
  changed = true;
  filter.configure(settings);
});

void loadFileSettings().then(
  (settings) => {
    if (!changed) {
      filter.configure(settings);
    }
  },
  () => filter.restore(),
);
function handlePageHide(event: PageTransitionEvent): void {
  if (!event.persisted) {
    stopObserving();
    stopSettings();
    filter.restore();
    window.removeEventListener("pagehide", handlePageHide);
  }
}
window.addEventListener("pagehide", handlePageHide);
