import { createApp } from "vue";
import "#popup/tokens.css";
import App from "./App.vue";
import { getMessage } from "#/i18n";
import { catalogLanguage } from "#/i18n";
document.title = getMessage("hideFilesTitle");
document.documentElement.lang = catalogLanguage();
createApp(App).mount("#app");
