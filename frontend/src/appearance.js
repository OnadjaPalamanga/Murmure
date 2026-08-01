const STORAGE_KEY = "murmure.appearance.v1";

export const APPEARANCE_DEFAULTS = {
  palette: "magenta-blue",
  density: 86,
};

const PALETTES = new Set(["magenta-blue", "rose-violet", "violet-azure"]);

function normalize(value = {}) {
  const density = Number(value.density);
  return {
    palette: PALETTES.has(value.palette) ? value.palette : APPEARANCE_DEFAULTS.palette,
    density: Number.isFinite(density)
      ? Math.min(96, Math.max(72, Math.round(density)))
      : APPEARANCE_DEFAULTS.density,
  };
}

export function loadAppearance() {
  try {
    return normalize(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}"));
  } catch {
    return { ...APPEARANCE_DEFAULTS };
  }
}

export function applyAppearance(value = loadAppearance()) {
  const appearance = normalize(value);
  const root = document.documentElement;
  root.dataset.palette = appearance.palette;
  root.style.setProperty("--window-alpha", (appearance.density / 100).toFixed(2));
  root.style.setProperty(
    "--sidebar-alpha",
    (Math.min(96, appearance.density + 2) / 100).toFixed(2),
  );
  return appearance;
}

export function saveAppearance(value) {
  const appearance = applyAppearance(value);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(appearance));
  return appearance;
}

window.addEventListener("storage", (event) => {
  if (event.key === STORAGE_KEY) applyAppearance();
});

