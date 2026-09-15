// The structural thresholds a component has to know about in JavaScript,
// mirroring `styles/_breakpoints.scss`. The reasons live there; keep the two
// files in step so a layout never changes at one width in CSS and another in
// the component that decides what to render.
//
// Prefer CSS for anything CSS can do. These exist only where the composition
// changes what is rendered — a pane that becomes a drawer, a control that
// becomes a different control — not to restyle something.
export const SPLIT = '(max-width: 1100px)';
export const PHONE = '(max-width: 860px)';
