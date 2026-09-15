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
  // No default section: an ordinary open lands on the category list on a phone
  // and on the popup's usual section on a wide screen. An explicit section, a
  // search result and a `/settings#section` bookmark all still open directly.
  const openSettings = (section) =>
    setRequest({ section: section || null, locationKey: location.key });
  const close = () => {
    setRequest(null);
    if (routed) navigate('/', { replace: true });
  };

  return (
    <SettingsContext.Provider value={{ openSettings, settingsOpen: open }}>
      {children}
      {/* `explicit` says whether a section was actually asked for: a phone
          opens the category list when it was not, and that section when it was. */}
      {open && (
        <Settings
          initialSection={
            routed ? location.hash.slice(1) || 'general' : request.section || 'appearance'
          }
          explicit={routed ? Boolean(location.hash.slice(1)) : Boolean(request.section)}
          onClose={close}
        />
      )}
    </SettingsContext.Provider>
  );
}
