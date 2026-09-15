// Run events record the shape of what a step produced, not the value: run logs
// live on disk and are shown in the dashboard, so a personal payload does not
// end up in either by default. These helpers turn that description into words
// for the canvas chip and the run log. The `Note in the run log` step is the
// deliberate exception — choosing it is how someone asks to see a value.

export function describeShape(value) {
  if (value === null || value === undefined) return 'nothing';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') return String(value);
  if (typeof value !== 'object') return 'a value';
  if (value.type === 'text')
    return `text, ${value.length} character${value.length === 1 ? '' : 's'}`;
  if (value.type === 'list')
    return `a list of ${value.length} item${value.length === 1 ? '' : 's'}`;
  if (value.type === 'object') return describeKeys(value.keys);
  return describeKeys(Object.keys(value));
}

function describeKeys(keys) {
  if (!keys?.length) return 'an empty result';
  if (keys.length <= 4) return keys.join(', ');
  return `${keys.slice(0, 4).join(', ')} and ${keys.length - 4} more`;
}

export function describeOutput(output) {
  if (!output || typeof output !== 'object') return null;
  const ports = Object.entries(output);
  if (!ports.length) return null;
  if (ports.length === 1 && ports[0][0] === 'out') return describeShape(ports[0][1]);
  return ports.map(([port, value]) => `${port}: ${describeShape(value)}`).join(', ');
}
