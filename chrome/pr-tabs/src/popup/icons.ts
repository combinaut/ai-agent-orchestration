export type ActionIconName = "menu" | "open" | "review" | "merge" | "copy";

export const ACTION_ICON_PATHS: Readonly<Record<ActionIconName, string>> = {
  menu: "M2 3.5h12v1.5H2zm0 3.75h12v1.5H2zM2 11h12v1.5H2z",
  open: "M8 2h6v6h-1.5V4.56L7.03 10.03 5.97 8.97 11.44 3.5H8zM3 4h3v1.5H4.5v7h7V11H13v3H3z",
  review:
    "M8 3a7 7 0 0 1 6.5 4.5A7 7 0 0 1 8 12 7 7 0 0 1 1.5 7.5 7 7 0 0 1 8 3m0 1.5a3 3 0 1 0 0 6 3 3 0 0 0 0-6m0 1.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3",
  merge:
    "M4 2.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4m0 7a2 2 0 1 1 0 4 2 2 0 0 1 0-4m1-6v6h3.5V7H12l-2.5-2.5L12 2v2H7v5.5h1.5V7H12l-2.5 2.5L12 12v-2H5",
  copy: "M5 2h7v8H5zm-2 3h1.5v6.5h6.5V13H3zM6.5 3.5v5h4v-5z",
};
