import PrimeVue from "primevue/config";
import { createApp } from "vue";
import App from "./App.vue";
import { getMessage, catalogLanguage } from "#/i18n";
import { primeVueConfiguration } from "#popup/primevue";
import "virtual:primevue-theme.css";
import "#popup/icons.css";
import "#popup/tailwind.css";

document.title = getMessage("settingsPageTitle");
document.documentElement.lang = catalogLanguage();
createApp(App).use(PrimeVue, primeVueConfiguration).mount("#app");
