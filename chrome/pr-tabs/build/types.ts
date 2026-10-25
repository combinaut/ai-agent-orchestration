export interface CommonTheme {
  style?: string;
}

export interface ThemeStyle {
  getCommonTheme: () => CommonTheme;
  getStyleSheet: () => string;
  getCommonThemeStyleSheet: () => string;
  getThemeStyleSheet: () => string;
  getLayerOrderThemeCSS: () => string;
}

export interface ThemeStyleModule {
  default: ThemeStyle;
}

export type IconSize = 16 | 32 | 48 | 128;
