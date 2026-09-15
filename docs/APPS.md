# Use apps in Vela

## Move around Vela

Home is your app launcher: it shows the apps you have installed and one place to
add another. Choosing an app opens it; if it is stopped, opening it starts it.

A narrow rail runs down the left of the dashboard: Home, Ask, a shortcut for
every installed app, then Library and a **More** menu holding Automations and
Manage apps. Settings sits at the bottom. Hover or tab to an icon to see its
name. The open destination is marked on the rail, so you always know where you
are.

On Home the rail stays on screen at every size, including on a phone, so a
ready app is always one tap away. Other pages move the same rail into a drawer
opened from the menu button in their header; the drawer also lists every
destination and installed app by name.

Apps that ask for it open beside the rail, with their name in the bar above
them; others open on their own. Either way, if an app has unsaved work, leaving
it asks before anything is lost. **App settings**, in that bar or in the app's
Vela menu, holds the app's permissions, its connection, updates and removal.

## On a phone

Vela is meant to be used from a phone as well as a computer. Tapping a field
does not zoom the page and leave it zoomed, and when the keyboard opens the box
you are typing in stays above it: the list or conversation above gives up the
space instead, keeping your place rather than jumping you somewhere else. A
dialog scrolls inside itself while the page behind it stays where you left it,
and reaching the end of a list does not start moving the page underneath.

You can still pinch to zoom anywhere, pan a zoomed page, and select and copy
text normally. Zooming in is not mistaken for the keyboard opening, so the
layout does not rearrange itself while you are magnifying it. The workflow
canvas is the one place with its own drag and pinch behaviour, and it keeps
visible zoom and fit controls for when you would rather tap.

Apps draw everything inside their own frame. Vela sizes that frame to the space
it can actually show and tells the app what is covered, so an app built on the
Vela starter behaves the same way. A website you have connected keeps its own
layout: Vela can give it the right amount of room, but it cannot change how that
site behaves on a phone.

iPhone and Android are treated with equal priority, in a browser tab and as an
installed Home Screen app. Where a release has been accepted on real hardware,
the devices and versions tested are named in the changelog; nothing here is a
claim that every phone, browser and version behaves identically.

## Show developer tools

Vela hides the technical side of your server until you ask for it. Turn on
**Settings → General → Show developer tools** to add app logs, process details,
runtime commands and ports, execution history, manual start and stop controls,
the **System** overview and the server-folder install source.

The switch changes only what this browser shows. It never changes an app's
permissions, starts or stops anything, or affects what Vela allows. It is
remembered in this browser alone, so your phone does not inherit the choice you
made on a desktop.

## Connect an existing web app

Run your web service first, using ServerKit, Docker or its own installer. In
**Library → Add an app → Connect a website**, enter its name, HTTPS address
and icon color. The
connection appears in Library, Manage apps and Home. Open it to use the service inside
Vela, with **Edit connection**, **Reload web app** and **Open in browser** in the
bar above it, always outside the service's frame.

Use a different hostname from Vela, for example `vela.example.com` and
`reading.example.com`. Different ports on the same hostname are insufficient:
browser cookies are shared across ports. Private-network HTTPS addresses are
supported, but every device needs to resolve/reach the address and trust its
certificate. `localhost` points to the device displaying the page, not necessarily
the Vela server. Plain HTTP addresses are not supported in this mode.

Sign in with the service's own account. If its page is blank, refuses embedding,
or login requires another site or blocked third-party cookies, choose **Open in
browser**. Vela does not override the service's frame restrictions. Navigation
inside the frame is limited to the configured origin (scheme, hostname and port);
other sites and sign-in providers may need a browser tab. Save your work before
leaving or reloading: connected services do not report unsaved changes to Vela.

Choose **Edit connection** from its view or details to change the name/address/color
or remove it. This only changes the saved connection. Vela does not install,
start, update, monitor, back up or delete the underlying service or its data.
Connection settings are saved on the Vela server and included in its database
backup. All clients of the same Vela server share these settings.

This is a host feature, separate from imported app packages. Connected web apps
do not receive Vela SDK sessions, storage grants or app actions.

## Wire apps together

**Automations** runs a set of steps for you — on a schedule, when a request
arrives, or when you press Run — and can ask an installed app to do one thing it
offers, such as Notes creating a note. You see the exact request and allow it
before anything runs. Automations run on the Vela server while the browser is
closed, and stop while Vela is not running. See
[the automations guide](AUTOMATIONS.md).

## Install and update

Open **Library → Import apps & refresh catalog**. Upload an app release ZIP or
enter the path of an app folder on the server computer. The package must have
`app.json` at its root. Review the source, version, permissions and any data
changes, then confirm the installation. A publisher name in a manifest is not
proof of a verified publisher.

To update, import a newer version with the same app ID. Save work in open app
views first, then reopen the app after updating. Vela stages the reviewed
package and rejects a stale review if the package or app data changes before
confirmation. Updating an app does not require rebuilding the server.

In the app's detail drawer, **Releases → Review rollback** restores a retained
package and its matching data checkpoint. Review the data that will be replaced
before confirming. The outgoing state is retained for recovery. Rollback is
not the same as keeping your latest edits with older app code.

## Health and older browser data

Health stores habit data on the server. If you used an earlier browser-only
version, open **Import earlier Health data** and review the earlier records.
Vela retains the original import and does not delete the browser copy.

Browser storage belongs to its exact address. If moving from HTTP to HTTPS or
to another hostname, download the recovery copy while visiting the old address,
then select that file at the new address. Keep the original until you have
verified the imported records.

If another browser saves first, Health preserves your draft and asks you to
resolve the conflict. Backups and restores use saved server data. Restoring an
app snapshot checks its revision and saves a recovery snapshot before replacing
data. Export downloads a JSON recovery copy; importing that file as an arbitrary
live database replacement is not currently supported.

## Meals and Notes

Install the current Meals and Notes apps. Each keeps its own server document.
Earlier Notes records can be imported through **Import earlier Notes data**.
Meals retains imported browser copies under **Earlier browser copies**; applying
one explicitly replaces the current plan and favorites after a revision check.

When Meals first asks, a line above the app says what it wants to do and where.
**Review** opens **App settings → Permissions**, which names the action, the app
it happens in, and what allowing it does and does not give away. Choose
**Allow**, then use **Send week to Notes** or **Send recipe to Notes**. The grant
allows that named write action, not access to Notes' existing records. **Stop
allowing** revokes it from the same place; changed installations or contracts
need a fresh grant.

If a send is interrupted, **Retry unfinished send** reuses the saved request key
so the same request does not create duplicate notes. The record of what has run
is a diagnostic: it appears under **App settings → Diagnostics** once developer
tools are on, and shows each action's status without exposing note contents.

## Ask about your server

**Ask** talks to the model server configured in Settings and can look at your
apps, their status, their logs and the engine — never the data inside your apps.

Conversations are kept on your Vela server while **Chat history** is on in
Settings → Chat & privacy. The panel beside the conversation lists them by date,
with search, rename, archive and permanent delete; archiving hides a conversation
and keeps it, deleting removes it for good. The conversation you are reading is
part of the address, so reloading the page or reopening the link returns to it,
and each conversation remembers a question you started typing but never sent.
Starting a new conversation keeps the previous one.

Turning chat history off deletes every stored conversation immediately, and
nothing new is written down while it stays off. The first time history is on,
a transcript your browser was holding from an earlier version is imported once.

## Connect an existing Ollama server

Install **Ollama Library**, open its connection controls, and enter the existing
server address. `http://127.0.0.1:11434` refers to the computer running Vela.
Choose **Test and connect**. The wrapper can list models, read model details
and report the Ollama version. It does not install Ollama, download models,
start or stop its server, or provide a chat interface.

Uninstalling the wrapper removes its Vela connection and session, not the
Ollama service or its models.

## Catalog and offline behavior

A new hub has no default catalog. An operator can configure `VELA_CATALOG`
with a local index or an HTTPS catalog plus its `VELA_CATALOG_SHA256` trust pin.
See [repository development](REPOSITORIES.md) for a sibling local catalog.

Refresh is explicit. Vela retains the last valid index and verified downloaded
archives, so previously downloaded releases can install offline. A release that
has never been downloaded cannot. Installed app code does not depend on its
original source folder remaining present.

Apps still need a reachable Vela Server for server data. There is no general
offline editing queue or automatic conflict resolution. Legacy v1 apps and
native processes are trusted code; new Library release imports require v2 and
do not run native process installation hooks.

See the [app contract](CONTRACT.md) for manifest and API details, and the
[server guide](SERVER.md#another-device) for access from another device.
