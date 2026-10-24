import { mount, type ComponentMountingOptions } from "@vue/test-utils";
import PrimeVue from "primevue/config";
import type { Component } from "vue";
import { primeVueOptions } from "#popup/primevue";

// Mounts a popup component with PrimeVue installed as main.ts does.
export function mountPopup<C extends Component>(
  component: C,
  options: ComponentMountingOptions<C> = {},
) {
  return mount(component, {
    ...options,
    global: { plugins: [[PrimeVue, primeVueOptions]] },
  });
}
