export interface ThemeStyle {
  getStyleSheet: () => string;
  getCommonThemeStyleSheet: () => string;
  getThemeStyleSheet: () => string;
  getLayerOrderThemeCSS: () => string;
}

export interface ThemeStyleModule {
  default: ThemeStyle;
}
