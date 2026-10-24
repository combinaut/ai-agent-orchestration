import PrimeVue from "primevue/config";
import { createApp } from "vue";
import App from "./App.vue";
import { primeVueOptions } from "./primevue";
import "./tailwind.css";

createApp(App).use(PrimeVue, primeVueOptions).mount("#app");
