import { Theme } from "@primeuix/themes";
import Aura from "@primeuix/themes/aura";
import type { Plugin } from "vite";
import type { ThemeStyleModule } from "./types";

const THEME_MODULE = "virtual:primevue-theme.css";
const RESOLVED_THEME_MODULE = `\0${THEME_MODULE}`;
const COMPONENTS = [
  "badge",
  "button",
  "iconfield",
  "inputicon",
  "inputtext",
  "message",
  "ripple",
  "select",
  "virtualscroller",
];

async function loadStyle(specifier: string): Promise<ThemeStyleModule> {
  return import(specifier) as Promise<ThemeStyleModule>;
}

function stripStyleTags(stylesheet: string): string {
  return stylesheet.replace(/<style\b[^>]*>|<\/style>/g, "");
}

// PrimeVue's SSR stylesheet APIs produce the same Aura variables and rules as styled mode.
export async function generateThemeCss(): Promise<string> {
  Theme.setTheme({
    preset: Aura,
    options: {
      darkModeSelector: "system",
      cssLayer: { name: "primevue", order: "theme, base, primevue, components, utilities" },
    },
  });

  const { default: base } = await loadStyle("@primevue/core/base/style");
  const stylesheets = [
    `${base.getLayerOrderThemeCSS()};`,
    base.getStyleSheet(),
    base.getCommonThemeStyleSheet(),
    base.getThemeStyleSheet(),
  ];

  for (const component of COMPONENTS) {
    const { default: style } = await loadStyle(`primevue/${component}/style`);

    stylesheets.push(style.getStyleSheet(), style.getThemeStyleSheet());
  }

  return stripStyleTags(stylesheets.join("\n"));
}

export function primeVueTheme(): Plugin {
  return {
    name: "pr-tabs-primevue-theme",
    resolveId(source: string): string | undefined {
      return source === THEME_MODULE ? RESOLVED_THEME_MODULE : undefined;
    },
    async load(id: string): Promise<string | undefined> {
      return id === RESOLVED_THEME_MODULE ? generateThemeCss() : undefined;
    },
  };
}
