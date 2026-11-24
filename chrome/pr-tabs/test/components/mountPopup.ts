import { mount, type ComponentMountingOptions } from "@vue/test-utils";
import PrimeVue from "primevue/config";
import { defineComponent, h, ref, type Component, type PropType, type Slots } from "vue";
import { primeVueConfiguration } from "#popup/primevue";
import type { PopupWrapper } from "#test/types";

interface MenuItem {
  separator?: boolean;
  disabled?: boolean;
  label?: string;
  command?: () => void;
}

function menuItemSlot(item: MenuItem, slots: Slots) {
  if (item.separator) {
    return h("li", { class: "p-menu-separator", role: "separator" });
  }

  return slots.item?.({
    item,
    props: {
      action: {
        class: "p-menu-item-link",
        onClick: () => {
          if (!item.disabled) {
            item.command?.();
          }
        },
      },
    },
  });
}

export const popupStubs = {
  teleport: true,
  transition: defineComponent(
    (_, { slots }) =>
      () =>
        slots.default?.(),
  ),
  Menu: defineComponent({
    name: "Menu",
    props: {
      model: { type: Array as PropType<MenuItem[]>, default: () => [] },
    },
    emits: ["hide"],
    setup(props, { attrs, emit, expose, slots }) {
      const open = ref(false);

      function hide(): void {
        open.value = false;
        emit("hide");
      }

      function toggle(): void {
        if (open.value) {
          hide();
        } else {
          open.value = true;
        }
      }

      expose({ hide, toggle });

      return () =>
        open.value
          ? h(
              "div",
              attrs,
              props.model.map((item) => menuItemSlot(item, slots)),
            )
          : null;
    },
  }),
};

// Mounts a popup component with PrimeVue installed as main.ts does.
export function mountPopup<C extends Component>(
  component: C,
  mounting: ComponentMountingOptions<C> = {},
): PopupWrapper<C> {
  const { global, ...options } = mounting;

  return mount(component, {
    ...options,
    global: {
      ...global,
      plugins: [[PrimeVue, primeVueConfiguration]],
    },
  });
}
