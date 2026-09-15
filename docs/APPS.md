# Use apps in Vela

## Connect an existing web app

Run your web service first, using ServerKit, Docker or its own installer. In
**Library → Add web app**, enter its name, HTTPS address and icon color. The
connection appears in Library, Apps and Home. Open it to use the service inside
Vela, with **Apps**, **Edit connection**, **Reload web app** and **Open in browser**
controls always outside the service's frame.

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

In Meals' host controls, open **App actions & activity** and allow **Create note**
in Notes. Then use **Send week to Notes** or **Send recipe to Notes**. The grant
allows that named write action, not access to Notes' existing records. It can
be revoked from the same controls; changed installations/contracts need a fresh
grant.

If a send is interrupted, **Retry unfinished send** reuses the saved request key
so the same request does not create duplicate notes. Activity shows the action's
status without exposing note contents.

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
