import { useRef } from 'react';
import Dialog from './Dialog.jsx';

export default function Drawer({ children, panelClassName = '', ...props }) {
  const panelRef = useRef(null);
  return (
    <Dialog closeOnBackdrop {...props} className="drawer-overlay" contentRef={panelRef}>
      <section ref={panelRef} className={`drawer${panelClassName ? ` ${panelClassName}` : ''}`}>
        {children}
      </section>
    </Dialog>
  );
}
