// The 3 by 3 unlock pattern's canonical rules.
//
// This mirrors `normalize_pattern` in `vela/access.py`, which is authoritative:
// the server re-normalizes whatever this file sends and refuses anything that
// breaks the rules. Keeping one copy here lets the drawing surface show the
// same shape the server will store, so a pattern never "works while drawing"
// and then fails to enrol.

export const DOTS = 9;
export const MIN_DOTS = 4;

// A straight move crosses another dot when both its rows and both its columns
// have the same parity; in a 3 by 3 grid that dot is always the average.
export function midpoint(from, to) {
  if (from === to) return null;
  const sameRowParity = (Math.floor(from / 3) + Math.floor(to / 3)) % 2 === 0;
  const sameColumnParity = ((from % 3) + (to % 3)) % 2 === 0;
  return sameRowParity && sameColumnParity ? (from + to) / 2 : null;
}

// The dots the server will record, or null when this is not a usable pattern.
export function normalizePattern(sequence) {
  if (!Array.isArray(sequence) || sequence.length < 2 || sequence.length > DOTS) return null;
  const dots = [];
  for (const value of sequence) {
    if (!Number.isInteger(value) || value < 0 || value >= DOTS || dots.includes(value)) return null;
    const previous = dots.length ? dots[dots.length - 1] : null;
    if (previous !== null) {
      const middle = midpoint(previous, value);
      if (middle !== null && !dots.includes(middle)) dots.push(middle);
    }
    dots.push(value);
  }
  return dots.length >= MIN_DOTS ? dots : null;
}

// What adding this dot would produce, used while a finger is still down. It
// returns the dots to append, so the trail matches what will be submitted.
export function appendDot(dots, dot) {
  if (!Number.isInteger(dot) || dot < 0 || dot >= DOTS || dots.includes(dot)) return dots;
  const previous = dots.length ? dots[dots.length - 1] : null;
  const middle = previous === null ? null : midpoint(previous, dot);
  return middle !== null && !dots.includes(middle) ? [...dots, middle, dot] : [...dots, dot];
}

export const samePattern = (a, b) => {
  const left = normalizePattern(a);
  const right = normalizePattern(b);
  return Boolean(left && right && left.join('-') === right.join('-'));
};
