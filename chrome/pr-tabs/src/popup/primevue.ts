import Aura from "@primeuix/themes/aura";
import type { PrimeVueConfiguration } from "primevue/config";

export const primeVueOptions: PrimeVueConfiguration = {
  theme: {
    preset: Aura,
    options: {
      darkModeSelector: "system",
      cssLayer: { name: "primevue", order: "theme, base, primevue, components, utilities" },
    },
  },
};
