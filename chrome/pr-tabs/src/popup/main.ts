import PrimeVue from "primevue/config";
import { createApp } from "vue";
import App from "./App.vue";
import { primeVueConfiguration } from "./primevue";
import "./tailwind.css";

createApp(App).use(PrimeVue, primeVueConfiguration).mount("#app");
