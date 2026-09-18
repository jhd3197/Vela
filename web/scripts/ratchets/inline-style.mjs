// Inline-style ratchet.
//
// A `style={{ }}` is a rule that no stylesheet can reach: a theme cannot
// recolour it, a media query cannot override it, and the partial that looks
// like it owns the element does not. Spacing, colour and typography belong in
// SCSS. Geometry computed at runtime — a pixel position from a drag, a width
// from a measured container, a percentage from live progress — does not, and
// those sites are named below with the reason each one keeps its inline style.
//
// Both populations are baselined, so `baseline.json` is the allowlist in its
// executable form: a new inline style anywhere fails, including in a geometry
// file, where it is a deliberate decision to record rather than a free pass.
import { matchLines, sourceFiles } from './_lib.mjs';

// Reasons, not exemptions: the runner counts these files like any other. They
// are listed so `--report` can say why each one is expected to stay.
export const GEOMETRY_REASONS = {
  'web/src/desk/grid/DeskGrid.jsx': 'board height and grid step from the measured cell size',
  'web/src/desk/ThemeRow.jsx': "a theme's own swatch colours, which arrived with the theme",
  'web/src/components/ds/Bars.jsx': 'each bar as tall as the value it is drawing',
  'web/src/components/ds/Meter.jsx': 'the fill, as far along its track as the number it shows',
  'web/src/components/ds/Sparkline.jsx': 'the line stretched to the cell it was given',
  'web/src/desktops/WindowFrame.jsx': 'window rectangle and resize cursor from window state',
  'web/src/desktops/SplitDivider.jsx': 'divider position from the split ratio',
  'web/src/desktops/EmptyPane.jsx': 'pane rectangle from the split bounds',
  'web/src/desktops/motion/GenieOverlay.jsx': 'animation surface sized to the source frame',
  'web/src/components/ui/ContextMenu.jsx': 'menu position from the pointer',
  'web/src/components/AppIcon.jsx': 'badge text scaled to the icon size it is drawn on',
  'web/src/components/UpdatesSection.jsx': 'the update download bar, filled from live progress',
  'web/src/pages/Launchpad.jsx': 'tile artwork at the icon size the desk is set to',
};

export default {
  id: 'inline-style',
  describe: 'A style={{ }} object in JSX.',
  fix: 'Move the rule into the component SCSS, or add the site to GEOMETRY_REASONS if it is runtime geometry.',
  scan() {
    const findings = [];
    for (const file of sourceFiles(['.jsx'])) {
      const reason = GEOMETRY_REASONS[file];
      findings.push(
        ...matchLines(file, /style=\{\{/g, () =>
          reason ? `inline style — geometry: ${reason}` : 'inline style',
        ),
      );
    }
    return findings;
  },
};
