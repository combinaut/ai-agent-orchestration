import type { PrimeVueConfiguration } from "primevue/config";

// Aura CSS is emitted by the build; keep styled component classes without runtime theming.
export const primeVueConfiguration: PrimeVueConfiguration = { theme: "none" };
