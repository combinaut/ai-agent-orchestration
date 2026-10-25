import PrimeVue from "primevue/config";
import { createApp } from "vue";
import App from "./App.vue";
import { localizeDocument } from "./localize";
import { primeVueConfiguration } from "./primevue";
import "./tailwind.css";

localizeDocument(document);
createApp(App).use(PrimeVue, primeVueConfiguration).mount("#app");
