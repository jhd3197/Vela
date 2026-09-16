// The one setting a placed widget can have.
//
// Only `volume` has options today: which of the user's volumes it reports.
// ServerKit's 647-line `WidgetEditor` inspector is deliberately not copied —
// there is nothing else to configure yet, and a full inspector for one select
// would be a worse thing to meet in Arrange mode.
import { useRef, useState } from 'react';
import Dialog from '../components/ui/Dialog.jsx';
import Button from '../components/ui/Button.jsx';
import FormField from '../components/ui/FormField.jsx';
import { useDeskData } from './DeskDataProvider.jsx';

export default function WidgetOptions({ widget, onSave, onClose }) {
  const { data } = useDeskData('metrics');
  const disks = data?.disks || [];
  const [path, setPath] = useState(widget.cfg?.path || disks[0]?.path || '');
  const cancel = useRef(null);

  const chosen = disks.find((disk) => disk.path === path);
  const save = () => {
    onSave({ ...widget.cfg, path, label: chosen?.label || '' });
    onClose();
  };

  return (
    <Dialog
      open
      onClose={onClose}
      closeOnBackdrop
      initialFocusRef={cancel}
      aria-labelledby="widget-options-title"
    >
      <h2 id="widget-options-title">Volume</h2>
      {disks.length === 0 ? (
        <p>
          No volumes are set up yet. Add one under Settings › Desk, then come back and choose it
          here.
        </p>
      ) : (
        <FormField label="Show">
          <select
            id="widget-options-path"
            value={path}
            onChange={(event) => setPath(event.target.value)}
          >
            {disks.map((disk) => (
              <option key={disk.path} value={disk.path}>
                {disk.label}
              </option>
            ))}
          </select>
        </FormField>
      )}
      <div className="form-actions">
        <Button ref={cancel} onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" disabled={!path} onClick={save}>
          Done
        </Button>
      </div>
    </Dialog>
  );
}
