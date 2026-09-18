// The bundled themes, read from the files the server ships rather than from a
// second copy: the suite that measures them in a browser has to be measuring
// what a user would actually get.
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = fileURLToPath(new URL('../../../vela/assets/themes/', import.meta.url));

export default readdirSync(dir)
  .filter((name) => name.endsWith('.json'))
  .sort()
  .map((name) => JSON.parse(readFileSync(path.join(dir, name), 'utf8')));
