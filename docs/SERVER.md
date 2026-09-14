# Vela Server

Vela runs on one computer and gives you a browser dashboard for your apps.
The server keeps running while you use the dashboard. Your apps and data stay
on that computer; closing a browser tab does not stop the server.

## Start

1. Extract the entire Vela Server archive into a folder of your choice.
2. On Windows, open `Vela.exe` inside the `Vela` folder. On macOS or Linux,
   run `./Vela` from that folder in a terminal.
3. The dashboard opens in your browser at **http://localhost:7700**.

The bundle includes Python, the server dependencies and the dashboard. You do
not need to install Python, Node.js, Git, or the separate developer repositories.
Keep the entire extracted folder together, including `_internal`.

Leave the server window open. Press **Ctrl+C** in it to stop Vela. Open Vela
again to start it. This initial portable release does not yet install a system
service or start automatically when you sign in.

## Add apps

Open **Library** and import an app release ZIP. Vela shows the permissions for
you to review before installing. A new server starts with an empty Library.
The app catalog and one-click discovery are separate from installing the server.

## Your data

Data is stored in `.vela` under your user home folder, outside the extracted
server folder. On Windows, this is `%USERPROFILE%\.vela`.

To update the server, stop it, extract the new version into a separate folder,
and launch that version. It uses the same data directory. Back up your data
before upgrading; replacing the server folder does not delete it.

## Another device

Phone, tablet and other computer browsers connect to the same Vela Server.
They do not each need their own server installation. The server computer must
stay on and reachable while you use its apps.

This preview opens locally by default. Network access currently needs a server
password and a trusted HTTPS certificate; there is no automatic network setup
wizard yet. From the extracted folder, run `Vela.exe --set-password` on Windows
(or `./Vela --set-password` on macOS/Linux), then start with your certificate:

```powershell
.\Vela.exe --host 0.0.0.0 --cert C:\certs\vela.pem --key C:\certs\vela-key.pem --origin https://vela.home:7700
```

Use the configured HTTPS address on your other devices and sign in. The
certificate must be trusted there. Vela does not configure DNS, certificates or
firewall access automatically. Once connected, you can save the dashboard to
your home screen. Saving it does not install another server or make app data
available while the server is offline.

For a headless launch, add `--no-open-browser`. See `Vela.exe --help` (or
`./Vela --help`) for other options.
