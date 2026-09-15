import { createContext, useContext, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Settings from '../pages/Settings.jsx';

const SettingsContext = createContext(null);

export const useSettingsPopup = () => useContext(SettingsContext);

// Keep the current page mounted, including an unsent chat or app state.
// Bookmarked /settings URLs still open the same popup over Home.
export default function SettingsProvider({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [request, setRequest] = useState(null);
  const routed = location.pathname === '/settings';
  const open = routed || request?.locationKey === location.key;
  const openSettings = (section = 'appearance') =>
    setRequest({ section, locationKey: location.key });
  const close = () => {
    setRequest(null);
    if (routed) navigate('/', { replace: true });
  };

  return (
    <SettingsContext.Provider value={{ openSettings, settingsOpen: open }}>
      {children}
      {open && (
        <Settings
          initialSection={routed ? location.hash.slice(1) || 'general' : request.section}
          onClose={close}
        />
      )}
    </SettingsContext.Provider>
  );
}
