// Mirrors the CSS custom properties in index.css (dataviz skill reference palette).
// Recharts needs literal color values for SVG fill/stroke, so we keep hex constants
// in sync with the CSS variables used elsewhere in the UI.
export const palette = {
  series1: "#2a78d6", // blue
  series2: "#eb6834", // orange
  series3: "#1baf7a", // aqua
  series4: "#eda100", // yellow
  series5: "#e87ba4", // magenta
  series6: "#008300", // green
  series7: "#4a3aa7", // violet
  series8: "#e34948", // red
  statusGood: "#0ca30c",
  statusWarning: "#fab219",
  statusSerious: "#ec835a",
  statusCritical: "#d03b3b",
  gridline: "#e1e0d9",
  textSecondary: "#52514e",
  textMuted: "#898781",
};
