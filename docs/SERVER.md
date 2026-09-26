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

## ServerKit

In ServerKit, create a service from `https://github.com/jhd3197/vela` and choose
the repository's `serverkit.yaml` configuration. Use `dev` until these files
are released on `main`. The Dockerfile builds the dashboard and Python server;
the `vela-data` disk stores installed apps, settings, credentials and backups
at `/data`. Keep that disk when redeploying. Automatic deployment on push is off.

Before starting the service:

1. Set `VELA_PUBLIC_ORIGIN` to your exact browser address, such as
   `https://vela.example.com`, with no trailing slash or path.
2. Set `VELA_TRUSTED_PROXIES` to the IP address of ServerKit's reverse proxy
   as seen from the container. For a host nginx proxy this is normally the
   Docker network gateway; find it in the container's network details. For a
   containerized proxy, use that proxy's address. Comma-separated IP addresses
   or narrowly scoped CIDRs are supported; `*` is rejected.
3. Retrieve `VELA_INITIAL_PASSWORD` from ServerKit's generated secret. It sets
   the Vela password on the first start only. An existing password survives
   redeployment; changing this variable does not reset it.
4. Add the matching domain in ServerKit, enable HTTPS, and route it to the
   service's HTTP port **7700**. The proxy must preserve the browser's `Host`
   header and overwrite `X-Forwarded-Proto` and `X-Forwarded-For` with the
   actual request scheme and client IP. Keep port 7700 private to the proxy.

Open your HTTPS address and sign in with the generated password. A new Library
is empty; import app releases to get started. ServerKit requests daily disk
backups with seven retained copies; check backup success in your panel.

The container requires both the HTTPS origin and trusted proxy configuration
and refuses to start without them. A healthy service with a failed sign-in
usually means the origin, proxy address or forwarding headers do not match.
`/api/health` is available over private HTTP for readiness checks; other API
requests require HTTPS through the trusted proxy.

Apps run inside this Linux container. Web apps and connections to existing
services work; native apps need their own runtimes and system dependencies
in the image. The image includes Python but does not expose the host's Docker
socket or desktop. See the [developer guide](DEVELOPMENT.md#container-build)
for building and checking the image.

## Add apps

Open **Library** and import an app release ZIP. Vela shows the permissions for
you to review before installing. A new server starts with an empty Library.
The app catalog and one-click discovery are separate from installing the server.

## Your data

Data is stored in `.vela` under your user home folder, outside the extracted
server folder. On Windows, this is `%USERPROFILE%\.vela`.

Hosted web apps keep their files under `.vela\managed\<app>\`: `releases\` holds
each version's program, `data\` holds the application's own database and
uploads, and `snapshots\` holds the backups you have taken of it. Removing an
app deletes its program and leaves `data\` alone; erasing its data is a separate
action. `managed.sqlite` records which apps are installed and which version each
one runs, and is part of Vela's own backup. The applications' data is not: back
those up from each app's page.

To update Windows, choose **Quit Vela** in the tray, then run the new installer.
Uninstall through Windows **Installed apps**; your apps and data stay intact.
For portable downloads, quit Vela, extract the new version into a separate
folder and launch it. Installed and portable copies use the same data directory.
Back up your data before upgrading. Do not run both copies at the same time.

Windows tray logs are in `%USERPROFILE%\.vela\logs\server.log`; use **Open logs**
from the tray menu if startup fails. A **Failed** status can mean port 7700 is
already in use. Stop the other server, then choose **Start server** to retry.

## App addresses

Each hosted web app is served by this same server under a name of its own, so it
can own its whole address the way it expects:

```
http://<app>.apps.localhost:7700
```

Browsers resolve `.localhost` names to this computer with no setup. Vela's
dashboard is never served on those names and the apps are never served on
Vela's, so an app cannot reach Vela's API even though they share a port.

To reach an app from another device, point a wildcard DNS entry for
`*.apps.vela.invalid` at this computer and install Vela's Wi-Fi certificate on
the device; the app then answers on `https://<app>.apps.vela.invalid`. That DNS
entry is yours to make -- Vela cannot create one -- and each app's settings say
so beside the address. Set `VELA_APP_DOMAIN` and `VELA_APP_LAN_DOMAIN` to use
names of your own instead.

## Another device

Phone, tablet and other computer browsers connect to the same Vela Server.
They do not each need their own server installation. The server computer must
stay on and reachable while you use its apps.

The welcome popup opens phone setup directly. Reopen it from
**Settings → Set up my phone**. If Vela is already hosted at an HTTPS domain,
the popup shows that domain's QR code immediately.

### Connect over the same Wi-Fi

1. On the Vela computer, open the welcome popup or **Set up my phone** in Settings.
2. Choose the computer's Wi-Fi address. If this is the first setup, choose a
   Vela password of at least 12 characters. Click **Enable Wi-Fi & show QR**.
3. Scan the QR code using your phone's Camera app and tap the link. Both devices
   must be on the same network. The QR uses the computer's network address,
   even when its dashboard is open at `127.0.0.1`.
4. The phone page guides the one-time certificate setup. On iPhone, open the
   page in Safari; another iPhone browser gets a link to copy into Safari.
   Compare the downloaded certificate's SHA-256 fingerprint with **Wi-Fi
   connection details** on the computer before trusting it.
5. Open **Open secure Vela** on the phone. Follow the Home Screen steps, then
   open the new Vela icon and sign in with your Vela password.

On iPhone, install the downloaded profile in **Settings → General → VPN & Device
Management**, then enable it in **General → About → Certificate Trust Settings**.
See [Apple's certificate trust instructions](https://support.apple.com/102390).
On Android, install it as a **CA certificate** in the phone's security/credential
settings; names vary by phone. Remove the Vela certificate if you stop using
that server.

Vela creates a certificate restricted to private network addresses, and serves
only setup instructions and the public certificate over HTTP on port **7701**.
Apps, passwords and sessions use HTTPS on port **7702**. The QR contains no
password or session. No router port forwarding is needed. If the phone cannot
connect, allow Vela through the computer's firewall on your private network;
guest Wi-Fi or device isolation can block the connection. Vela does not change
firewall rules or install certificates on your devices automatically.

Wi-Fi access resumes when Vela restarts. Keep the computer on and reachable.
If its network address changes, reopen setup, choose the new address and scan
again; reserving its address in your router avoids changing saved phone URLs.
Under **Wi-Fi connection details**, choose **Turn off Wi-Fi access** to disconnect
phones and stop both listeners while keeping the desktop dashboard available.

### Lock your phone's Vela session

On a phone or any browser that signs in with your Vela password, open
**Settings → Security** and turn on **App lock**. Vela asks for your password,
then for a six-digit PIN — or a pattern connecting at least four of nine dots —
twice. After that, opening Vela on that device asks for the PIN or pattern
instead of the full password.

Choose how soon it locks by itself under **Lock after inactivity**: 1, 5 or 15
minutes, 5 by default. **Lock now** locks it immediately. Changing the method,
changing the timer and turning app lock off each ask for your Vela password
again, and your password always works on the lock screen. After five wrong
attempts only the password is accepted; reloading the page, opening another tab
or changing network does not reset that count.

The server enforces the lock. While a session is locked its apps, data,
automations and open chats stop answering, including app connections that were
opened before it locked. Background updates do not count as activity, so a phone
left on a table still locks on time. The PIN or pattern is never written to disk
and never kept in the browser, so signing out, a server restart or a new access
password ends app lock and asks for the Vela password again.

App lock protects the browser session it was set up in. It does not encrypt the
files on your Vela computer and does not lock that computer's screen — use the
computer's own screen lock for that. The Vela computer's own dashboard opens
without signing in, so there is no session there to lock.

### Save Vela to your Home Screen

- **iPhone / iPad:** In Safari, open **Share** (possibly inside **More (…)**), then
  **Add to Home Screen**. If needed, find it in **Edit Actions**. Leave **Open as
  Web App** on when offered, then tap **Add**.
- **Android:** In Chrome, open the three-dot menu and choose **Add to Home screen**
  or **Install app**. Confirm **Install** or **Add**. When supported, Vela also
  offers the browser's install button directly.

See [Apple's Home Screen instructions](https://support.apple.com/guide/iphone/iphea86e5236/ios)
and [Chrome's Android web app instructions](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DAndroid).
Saving Vela does not install another server or make app data available while
that server is offline.

### Use an existing HTTPS certificate

For a custom domain or managed network, you can still start Vela with your own
certificate. Quit the running server, then use `Vela.exe --set-password` on
Windows (or `./Vela --set-password` on macOS/Linux). Start with:

```powershell
.\Vela.exe --host 0.0.0.0 --cert C:\certs\vela.pem --key C:\certs\vela-key.pem --origin https://vela.home:7700
```

Open that HTTPS address on the computer. Phone setup uses it for the QR and
skips the built-in Wi-Fi certificate guide. DNS, certificate trust and firewall
access must already be configured for that address.

## Other launch options

For a console or headless launch, use `--no-tray --no-open-browser` from a terminal.
See `Vela.exe --help` (or `./Vela --help`) for other options. Custom `VELA_DATA_DIR`
launches cannot enable start at sign in through the tray, because that environment
would not automatically be carried into your next Windows session.

The Windows tray starts after sign-in; it is not a system service. Background
services before sign-in and automatic download/install of updates are not yet implemented.
