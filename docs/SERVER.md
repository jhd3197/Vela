# Vela Server

Vela runs on one computer and gives you a browser dashboard for your apps.
The server keeps running while you use the dashboard. Your apps and data stay
on that computer; closing a browser tab does not stop the server.

## Windows

1. Download the `windows-x64-setup.exe` file from [Releases](https://github.com/jhd3197/Vela/releases).
2. Run the installer, then open **Vela** from Start.
3. The dashboard opens in your browser at **http://localhost:7700**.

Vela runs in the notification area beside the clock. Right-click its sail icon
to open the dashboard, start or stop the server, open logs, or quit Vela.
**Start at sign in** is optional and off by default; enable it in the installer
or tray menu. It starts the server quietly when you sign in to Windows.

The installer is for Windows 10/11 x64 and installs for your Windows account
under `%LOCALAPPDATA%\Programs\Vela`; administrator access is not required.
Downloads are currently unsigned, so Windows may show an unknown-publisher prompt.

Prefer no installation? Extract the entire Windows ZIP and open `Vela.exe`
inside the `Vela` folder. It has the same tray controls. Keep the extracted
folder in place if you enable start at sign in; disable that option before
moving or deleting the portable folder.

## macOS and Linux

Extract the archive and run `./Vela` from the `Vela` folder in a terminal.
Leave that terminal open; press **Ctrl+C** to stop the server.

The bundle includes Python, the server dependencies and the dashboard. You do
not need to install Python, Node.js, Git, or the separate developer repositories.
Keep the entire extracted folder together, including `_internal`.

## Add apps

Open **Library** and import an app release ZIP. Vela shows the permissions for
you to review before installing. A new server starts with an empty Library.
The app catalog and one-click discovery are separate from installing the server.

## Your data

Data is stored in `.vela` under your user home folder, outside the extracted
server folder. On Windows, this is `%USERPROFILE%\.vela`.

To update Windows, choose **Quit Vela** in the tray, then run the new installer.
Uninstall through Windows **Installed apps**; your apps and data stay intact.
For portable downloads, quit Vela, extract the new version into a separate
folder and launch it. Installed and portable copies use the same data directory.
Back up your data before upgrading. Do not run both copies at the same time.

Windows tray logs are in `%USERPROFILE%\.vela\logs\server.log`; use **Open logs**
from the tray menu if startup fails. A **Failed** status can mean port 7700 is
already in use. Stop the other server, then choose **Start server** to retry.

## Another device

Phone, tablet and other computer browsers connect to the same Vela Server.
They do not each need their own server installation. The server computer must
stay on and reachable while you use its apps.

The dashboard shows a welcome popup on the first visit in each browser. Choose
**Set up my iPhone** to connect your phone, or **Stay on this computer** to
continue. Reopen the guide from **Settings → Set up my iPhone** at any time.

This preview opens locally by default. Network access needs a server
password and a trusted HTTPS certificate. The welcome guide explains these
requirements; it does not configure network access automatically.
Quit the running server first. In a terminal in the installed or extracted folder, run `Vela.exe --set-password` on Windows
(or `./Vela --set-password` on macOS/Linux), then start with your certificate:

```powershell
.\Vela.exe --host 0.0.0.0 --cert C:\certs\vela.pem --key C:\certs\vela-key.pem --origin https://vela.home:7700
```

Use the configured HTTPS address on your other devices and sign in. The
certificate must be trusted there. Vela does not configure DNS, certificates or
firewall access automatically. Once connected, you can save the dashboard to
your home screen. Saving it does not install another server or make app data
available while the server is offline.

### Save Vela on your iPhone

1. Open your configured HTTPS Vela address on the server computer and sign in.
2. Choose **Set up my iPhone** in the welcome popup or Settings. Scan its QR
   code with the iPhone Camera app and tap the link. Use the same Wi-Fi or a
   network that can reach your server.
3. The setup page detects Safari and shows the Home Screen steps. If it detects
   another browser, copy the link into Safari. If detection is wrong, choose
   **I'm already in Safari** to see the steps.
4. In Safari, open **Share** (possibly inside **More (…)**), then **Add to Home
   Screen**. If needed, find it in **Edit Actions**. Leave **Open as Web App** on
   when offered, then tap **Add**.
5. Open the Vela icon on your Home Screen and sign in if asked.

The QR contains only the setup address, not your password or session. The
instructions can open before sign-in; your apps still require authentication.
The wizard offers a QR only for a server with phone access enabled, since a
`localhost` address on your computer would point to the phone itself when scanned.
See [Apple's Home Screen instructions](https://support.apple.com/guide/iphone/iphea86e5236/ios)
for Safari's current menu options.

## Other launch options

For a console or headless launch, use `--no-tray --no-open-browser` from a terminal.
See `Vela.exe --help` (or `./Vela --help`) for other options. Custom `VELA_DATA_DIR`
launches cannot enable start at sign in through the tray, because that environment
would not automatically be carried into your next Windows session.

The Windows tray starts after sign-in; it is not a system service. Background
services before sign-in and automatic download/install of updates are not yet implemented.
