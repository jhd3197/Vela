// Settings-kit ratchet.
//
// Settings drifted because every section laid itself out with whatever classes
// were nearby: a fact grid here, a form grid there, On/Off buttons in one place
// and a checkbox in the next, an error above the fields in one section and
// below them in another. `components/settings/SettingsKit.jsx` is the one way
// a settings surface is built now, and `_settings-kit.scss` the one stylesheet
// for it.
//
// A settings surface is `pages/Settings.jsx` and any module that imports the
// kit. In one, this flags the layout classes the kit replaced, a bare checkbox
// (a yes/no setting is a `toggle`), and an On/Off button pair (same). Dialogs
// and drawers opened from Settings keep their own conventions; the few they
// use are in the baseline, and the number can only come down.
import { readLines, sourceFiles } from './_lib.mjs';

const KIT = 'web/src/components/settings/SettingsKit.jsx';
const PAGE = 'web/src/pages/Settings.jsx';

// The classes the kit replaced, each with what to use instead.
const REPLACED = {
  'settings-row': 'SettingRow',
  'settings-row-stack': 'SettingRow',
  panel: 'SettingsGroup',
  'panel-head': 'SettingsGroup title/aside',
  'panel-note': 'SettingsGroup description or SettingsNote',
  'panel-lead': 'SettingsGroup',
  'panel-follows': 'SettingsGroup',
  'panel-qualifies': 'SettingsNote',
  'fact-grid': 'SettingRow value',
  fact: 'SettingRow value',
  'fact-wide': 'SettingRow value',
  'form-grid': 'SettingRow htmlFor/control',
  field: 'SettingRow htmlFor/control',
  'field-row': 'SettingRow htmlFor/control',
  'field-side': 'set-field-pair',
  'field-label': 'SettingRow htmlFor',
  'form-actions': 'SettingsActions',
  actions: 'SettingsActions',
  'inline-error': 'SettingsStatus tone="error"',
  'saved-note': 'SettingsStatus tone="ok"',
  'mini-list': 'SettingRow per item',
  'personalise-row': 'SettingRow toggle',
  'security-row': 'SettingRow onClick',
  'security-rows': 'SettingsGroup',
  'section-head': 'SettingsGroup title',
};

const CLASS_ATTR = /className=(?:"([^"]*)"|\{`([^`]*)`\})/g;
const CHECKBOX = /type=["']checkbox["']/g;
// A button label chosen by a loop variable, `{value ? 'On' : 'Off'}`, as a
// child; `value={enrolled ? 'On' : 'Off'}` is a row's read-only state.
const ON_OFF = /(?<!=)\{\s*\w+\s*\?\s*['"]On['"]\s*:\s*['"]Off['"]\s*\}/g;

function surfaces() {
  return sourceFiles(['.jsx']).filter(
    (file) =>
      file === PAGE ||
      (file !== KIT && readLines(file).some((line) => line.includes('settings/SettingsKit.jsx'))),
  );
}

export default {
  id: 'settings-kit',
  describe:
    'A settings surface (Settings.jsx or a module importing SettingsKit.jsx) using a layout ' +
    'class the kit replaced, a bare checkbox, or On/Off buttons.',
  fix:
    'Build it from SettingsGroup / SettingRow (toggle, control, value, onClick) and put ' +
    'errors in SettingsStatus in the group footer. See components/settings/SettingsKit.jsx.',
  scan() {
    const findings = [];
    for (const file of surfaces()) {
      readLines(file).forEach((text, index) => {
        const line = index + 1;
        for (const match of text.matchAll(CLASS_ATTR)) {
          // Template literals: only the static words count, not `${...}`.
          const value = (match[1] ?? match[2]).replace(/\$\{[^}]*\}/g, ' ');
          for (const name of value.split(/\s+/)) {
            if (REPLACED[name]) {
              findings.push({ file, line, key: `.${name} (use ${REPLACED[name]})` });
            }
          }
        }
        if (CHECKBOX.test(text)) findings.push({ file, line, key: 'checkbox (use toggle)' });
        if (ON_OFF.test(text)) findings.push({ file, line, key: 'On/Off buttons (use toggle)' });
        CHECKBOX.lastIndex = 0;
        ON_OFF.lastIndex = 0;
      });
    }
    return findings;
  },
};
