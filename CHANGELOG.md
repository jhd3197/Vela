# Changelog

Notable changes to Vela Server and its browser dashboard. SDKs and individual
apps are versioned in their own repositories. Changes stay under Unreleased
until the release workflow prepares a tested server version.

## Unreleased

### Changed

- **Existing 0.1.x servers need one manual update.** The updater ships *in*
  this release, so a server running an earlier version cannot use it to get
  here. Download this release the way you installed Vela originally — the
  installer, the portable zip or the archive — and from then on Vela can update
  itself.
- **Settings and app-state files keep a spare copy.** Each time Vela writes one
  it keeps the previous good version beside it, so a file that will no longer
  read can be put back from Settings → Health instead of falling back to
  defaults. Your desk is no longer one of these files — it lives with your
  desktops now and is covered by backups instead.
- **Apps open in windows on your desk.** Opening an app — from the rail, All
  apps, a desk widget, search or a shortcut — now puts it in a window rather
  than filling the screen. It gets a title bar with three controls that do three
  different things: **minimize** puts it away and nothing else — the app keeps
  running and whatever you had typed is still there when you bring it back;
  **maximize** fills the desk; **close** is the only one that ends the window,
  and the only one that asks about unsaved work. Windows can be moved, resized
  and snapped to a half, they stay where you left them, and each desktop keeps
  its own. Opening an app that already has a window brings that window forward
  instead of opening a second copy of it.

  Some apps still open full screen, because a window is not the right answer for
  them: anything on a phone, an app that opens outside Vela, a connected site,
  and an app you have not installed yet. You can ask for the full-screen page
  for any app — right-click it in All apps and choose **Open full screen** — and
  a link straight to an app still opens it that way.

- **Windows travel to and from the rail.** Minimizing shrinks the window into
  its icon in the rail and bringing it back reverses the same motion, so you can
  see where it went. Vela does not read an app's pixels to do this — a browser
  will not let this page do that, and weakening an app's sandbox for an
  animation would be trading a real boundary for a decoration — so the window
  itself moves, with the app still running inside it. Reduced motion skips it.

- **The rail names what is open.** Windows on the desktop you are looking at
  appear in the rail under **Open**, showing which one is selected and which are
  minimized; clicking one brings it forward, and clicking the one you are in
  puts it away. Apps whose process is running without a window here appear
  separately under **Running**, and no app is listed in both. A window whose app
  was reinstalled says it needs reopening rather than pretending to still be
  connected.

- **All apps opens over what you were doing.** The app grid used to be a page,
  so going to look for an app left the one you had open. It is now a layer over
  the current page: the app underneath stays exactly as it was, with whatever
  you had typed still in it, and Escape or the rail puts you back. The rail
  entry is called **All apps** now; Ctrl+Space (Cmd+Space) and the `/apps` link
  both still open it.

- **The server log is readable again.** Vela no longer records a line for every
  request the dashboard makes, so its log holds what Vela did rather than
  thousands of routine polls. Both ways of starting Vela — the tray on Windows
  and `python -m vela` in a terminal — now write the same log, where before
  only the tray kept one, and it keeps five 2 MB files instead of three.

- **The desk is a desktop now.** The search sits in the middle of the wallpaper
  where a launcher's does, and the buttons that used to crowd it moved into a
  **⋯** menu at the top-right and a **right-click** (long press on a phone) on
  the wallpaper: Add widget, Arrange desk, Personalise, and **Reset desk** to
  put a board back the way Vela ships it. Right-click a widget for its own
  quick menu — open its app, resize it, or remove it — without arranging the
  whole board. A fresh desk fills the screen with no bare gaps, and your apps
  show as a labelled icon grid with **Open all** leading to the Launchpad.
  The desk and the Launchpad no longer draw a blurred bar across the top of
  the wallpaper: the search, the notification bell and the **⋯** menu share
  one line at the top, and the page runs the full height behind them.
- **Apps open in a real window.** An app you open now has its own title bar: a
  back arrow to where you came from, its name and whether it is running, and a
  **⋯** menu to pin it to the rail, add its widget to your desk, reach its
  settings, reload it, open it in a new tab, stop it or close it — no more second
  search box above the app. While an app is starting you see it over a dimmed
  background, and if it never answers Vela says so and offers to reload or stop
  it. Opening an app that is no longer installed leads back to the Marketplace.
  An app can ask (with `view.appearance`) for its title bar to stay dark so a
  dark app is not topped by a light strip.
- **The Library is now the Marketplace.** One place — still at the same
  address — to find, install, update and remove apps, in three tabs. **Discover**
  shows the apps you do not have yet, led by a small featured row. **Installed**
  is everything on this computer, connected websites included, where you open an
  app to update or remove it. **Updates** gathers the apps with a newer release
  and can update them all at once. The separate Manage apps page is gone; its job
  lives in the Installed tab.
- **The rail is yours to arrange.** It now shows the apps you pin plus whatever
  is open, instead of every app you have installed. Desk and the Launchpad stay
  at the top; below them are your pinned apps — Ask and the Marketplace to start —
  then the apps that are open but not pinned, then Settings. Right-click an app
  on the rail or in the Launchpad (or press and hold on a phone) to pin, unpin
  or reorder it. Vela's own tools — Ask, Automations, the Marketplace, Settings and
  System — are core apps now: they show in the Launchpad under **Vela**, they
  turn up in search, and they can be pinned like any app. The old **More** menu
  is gone.
- **Promoting dev to main now builds the downloads first.** A pull request to
  `main` runs the release the way the release workflow will: it runs the
  release script and builds and smoke-tests the server on Windows, Linux and
  macOS, stopping short of tagging or uploading anything. Release breakage that
  used to surface only after a merge — a script that fails to start, a download
  missing for a runner's architecture — now shows on the promotion PR instead.
  Contributors working on `scripts/` should expect that PR to take longer than
  a routine `dev` check.

### Fixed

- **The rail avatar draws properly.** Its stylesheet was never loaded, so the
  letter standing for whoever the server belongs to appeared unstyled.

- **The wallpaper no longer disappears when you close All apps.** The desk and
  the All apps grid are on screen at the same time and both ask for the same
  picture; closing the grid cleared it for the desk underneath as well, leaving
  a plain background until something else happened to set it again.

- **Apps installed by an older Vela can be opened in a window.** Installing an
  app records which installation of it this is, and windows are opened against
  that record — but apps that were already installed before Vela started keeping
  it had none, so a window could not be opened for them at all. Vela now gives
  every installed app that record when it starts. Nothing is reinstalled and no
  app data is touched; an app you removed is not brought back.

### Added

- **Agent desktops are in the download.** A Vela download now carries the piece
  that drives an agent's browser, along with the record of which browser build it
  was made against and a checksum of its own code that Vela verifies before
  starting it. Two halves from different downloads are refused with a sentence
  about reinstalling rather than failing later in a way that looks like a bug in
  whatever the agent was doing.

  The browser itself is not in the download — it is a few hundred megabytes and
  lives in a cache shared with anything else that uses one. Vela says which
  command fetches it and will never download a browser while you are waiting for
  a task.

- **Agent desktops tidy up after themselves, and say so.** Four things pile up
  while one works and they now have four stated lifetimes: a picture of a window
  lasts two minutes, a record that something happened lasts a day, a file lasts a
  week, and tasks last as long as you keep history. Vela sweeps what has expired
  when it starts and periodically after that.

  Turning off **Keep history** now reaches everything, not only the task list:
  the activity under each task goes with it.

  A backup covers your settings, your desktops and what your apps saved — it is
  not a copy of a browser session, so a sign-in a desktop was keeping, a file
  staged for a task and a picture of a window are all outside it. Restoring one
  stops every agent desktop first, drops every permission they held, and reports
  what was running as interrupted rather than resuming it.

  **Settings → Health** has three new checks — whether agent desktops can run
  here and against which browser, how much room their files are using with a
  button to clean up, and whether any browser is open for a desktop that is no
  longer an agent's. A support bundle now carries those numbers and nothing else
  about the feature: no task wording, no results, no websites you approved, no
  sign-in, no file and no picture of a window.

- **Windows move the way they should.** Minimizing and restoring an agent's
  window now plays a short warp toward its entry in the rail — 480 milliseconds,
  the same in both directions, and it narrows toward the icon rather than sliding
  as a rectangle.

  It happens where Vela actually has a picture of the window to move, which
  means a window the agent's own browser is rendering. An ordinary app on your
  own desktop runs in a frame this page is not allowed to read the pixels of;
  that is a browser rule, not a missing feature, and Vela will not ask for screen
  recording or weaken an app's isolation to get around it for an animation —
  those windows minimize immediately instead.

  Ask your system for reduced motion and there is no warp at all. Either way it
  is decoration over a decision that has already happened: minimizing a window
  never pauses a task, ends a session or stops an agent, and the window goes
  where you put it whether or not anything was drawn. A picture of the agent's
  screen that has gone stale now says how old it is, and Stop never depended on
  that stream.

- **Two windows side by side, and everything a drag does without one.** Drag a
  window's title bar to the left or right edge and Vela shows the half it would
  take; let go and it goes there. The other half either keeps the window it had
  or becomes a clearly empty pane that offers to be filled — it never quietly
  shows the same window twice, and closing or minimizing one member leaves its
  slot there so the window can come back into it.

  The divider between the panes can be dragged, moved with the arrow keys, sent
  back to equal halves with Home or a double-click, and it announces where it is
  to a screen reader. A **⋯** menu on every title bar does the same things a drag
  does — move left, move right, swap the panes, even them up, leave split view —
  so none of this needs a pointer.

  Dragging an app onto the desk still makes a widget of it, and dragging one into
  a task's box now names it for that task. What travels is the app's identity and
  nothing else: no install happens, nothing is submitted, and what the desktop is
  allowed to use does not change. There is a **Mention an app** picker beside the
  Start button for anyone not dragging.

- **Two desktops can work at once, and a queue stops when something goes wrong.**
  Vela runs two agent desktops at a time on one computer; a third is refused with
  a sentence, and a task waiting for one of the two says it is waiting instead of
  showing *Starting* for ten minutes. Each desktop keeps its own queue, its own
  limits and its own window in front, and nothing one does reaches the other.

  A task that failed, was interrupted or sent something nobody could confirm now
  **holds** the rest of that desktop's queue and says why, rather than starting
  the next one on top of it. **Carry on** releases it, and so does giving the
  desktop something new to do. A result that did not succeed says how far it
  actually got — the last step there is a receipt for.

  **Try again** queues the same instruction as a new task. It is never the old
  one carrying on: what that one did, it did. A desktop with something
  unaccounted for will not repeat it until you say you have checked.

  Updating, removing or upgrading an app now takes its authority with it. Every
  permission an agent desktop held against that app is dropped and any question
  waiting about it can no longer be answered, so an approval reviewed against one
  version of an app can never apply to another.

- **An agent desktop can use a website, and bring a file back.** Approving a
  site is two decisions now, so the setup asks two questions. **Reading only**
  is the default: it can browse, and signing in, sending a form or anything else
  that would change something there stops and asks you to take over. **Ask me
  before it sends anything** holds the request before it leaves your computer and
  shows you what it is — the address, the method and the *names* of the fields,
  never the values, because a prompt gets screenshotted. Saying yes allows that
  exact request and nothing else. Requests Vela cannot describe honestly are not
  offered as a question at all; they ask for a person.

  Signing in is yours. Vela will not attempt one and will not try to get past a
  challenge meant to tell people from programs — it stops, says so, and hands you
  the window. A desktop forgets website sign-ins when its browser closes unless
  you turn on keeping them, and what is kept belongs to that desktop alone: two
  agent desktops are never signed in as each other, your own browser's profile is
  never read, and **Erase** clears both the file and the browser that is open.

  Files go through your own picker. The agent is told which files exist and can
  attach one to a page that asks for one; there is no tool that takes a path, so
  there is nothing to point elsewhere. Anything it downloads from an approved
  site appears in the same list with where it came from. Up to 25 MB for a file
  you add, 100 MB for a download and 250 MB for one task, shown before a transfer
  rather than discovered by one failing. A file is stored under a name Vela
  generated, nothing is extracted and nothing is run.

  **If something was sent and no answer came back, Vela says so.** The task ends
  with *Outcome unknown*, the Files panel shows what went out, and nothing sends
  it again until you say you have checked. One honest limit: when a connection
  dies before any reply, the browser itself may resend below anything Vela can
  intercept, so the site may have received it more than once — which is exactly
  why checking is a person's job and not a retry. See
  [agent desktops](docs/AGENT-DESKTOPS.md).

- **Watch what the agent is doing, and take the keyboard when you want it.**
  The Agent window shows the actual screen the agent is working on. Watching is
  watching: clicking the picture does nothing. **Take over** stops the agent
  first, then hands you the window — your clicks and keys go to it, and the task
  stays paused until you say carry on, even after you give control back. Only one
  person can be typing at a time, and anyone else watching can see who it is.
  When the agent does carry on, it looks at the screen again before doing
  anything, so it sees what you changed.

  Closing the tab does not hand the keyboard to the agent; control simply times
  out. Pause holds a task where it is, Stop ends it — and neither pretends to
  undo something that already happened. Vela never reads your clipboard.

- **Set up a desktop that works for you, and watch it.** The rail's desktop menu
  has an Agent button on every desktop. On one that is still yours it opens a
  short setup — which model, which apps, which websites, and whether changes ask
  first — and it will not let you start a desktop that could not work: a missing
  model server or a model that cannot operate an app is said plainly, next to the
  choice, before you commit to anything. Asking before changes is the default,
  and the other option allows only the actions you pick.

  Afterwards the same window is where you give it work. It shows what is being
  worked on, how long it has actually been working, what is queued behind it and
  what came of earlier tasks, with Pause, Carry on and Stop. A change that needs
  you appears here as a card saying what would change, with Allow, No, and Allow
  for an hour — and this window is the only place that can answer it. Every
  result says whether anything was really changed, read from what happened rather
  than from how the task described it. Desktops working in the background show a
  mark on the rail, so you can see that Desktop 2 needs you while you are on
  Desktop 1.

  If you have notifications set up, Vela sends one line when a task finishes or
  needs you — not one per click — and nothing at all for a task you stopped
  yourself. There is a new switch for it beside the others.

- **A desktop can carry out a task while you do something else.** Give an agent
  desktop an instruction and Vela works on it on the server: you can close the
  dashboard, switch to another desktop or shut the browser, and the task carries
  on. It runs inside the limits the desktop's settings give it — how many steps,
  how long, how many times it may ask the model — and when it reaches one it
  stops and says which. Time spent waiting for you to approve something is not
  charged against it. Pause, resume and stop are yours; stopping ends what has
  not happened yet and never pretends to undo what has.

  What a task reports is checked against what it actually did. A summary that
  claims a change with nothing to show for it is refused, and every result
  records whether anything really changed — read from the receipts, not from the
  wording. Vela never restarts a task by itself: if the server stops mid-task it
  says the task was interrupted and leaves anything queued for you to start.

  **Not every model can do this.** A task needs a model that supports tool
  calling, and Vela checks before opening anything rather than failing halfway
  through. Beyond that, models differ a great deal in whether they can actually
  work an app, and a bigger model is not automatically better at it. See
  [evaluating a model](docs/DEVELOPMENT.md#evaluating-a-model).

- **A change can wait for you to say yes.** When an app running on an agent's
  desktop tries to save something the desktop was not already allowed to save,
  the change becomes a question rather than a failure. Nothing is written while
  it is open, the question says in plain language what would change — and
  describes a password-like field rather than repeating it — and saying yes
  approves that exact change and no other. Say no, close the window, change the
  desktop's permissions or stop the task, and a click arriving afterwards does
  nothing at all. Questions expire on their own, and no amount of asking for
  more time pushes that past twenty minutes.

  Apps are not left hanging while you decide. An app that loads Vela's own SDK
  waits as long as the question does; one that bundles an older copy is told
  straight away that it cannot wait, so you can make the change yourself in the
  window instead. Your own clicks are unaffected: using your own computer has
  never needed anyone's approval and still does not.

- **App developers: label your controls and declare your actions.** Vela is
  building desktops an agent can work in, and the parts that read and operate an
  app's window now exist. An app is found by the accessible name of its
  controls — `aria-label`, a real `<label>`, `placeholder`, `title`, then its
  text — so a button whose only name is an icon cannot be used, and a control
  that renames itself while it is on screen is refused rather than clicked. A
  named action is preferred to a form wherever one fits: it is validated, it
  returns a receipt, and repeating its request key returns the first answer
  instead of doing the work twice. Nothing changes for an app that is not in an
  agent's window, and an app in one is told so through `view.chrome`. Whatever
  an app saves still goes through the same permission check whether a person
  clicked it or an agent did. See
  [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#what-a-run-can-perceive-and-do).

- **Your desk is a desktop you can have more than one of.** The desk you have
  becomes **Desktop 1** with every widget, both the wide and the phone
  arrangement, and its wallpaper exactly where they were; nothing is asked of
  you and the old `desk.json` is left untouched. You can now keep up to 16 of
  them, each with its own widgets and its own picture, and each one is a
  workspace rather than a copy: your apps and everything they have saved stay
  shared, so deleting a desktop never deletes a note. Backups now carry your
  desktops, so restoring one brings back how your desk was arranged. The
  A **desktops button in the rail** names the one you are looking at and
  switches between them, with New, Rename and Delete in the same menu. Which
  one you are on belongs to the device you are on: choosing Desktop 2 on your
  laptop leaves your phone where it was. Each one keeps its own widgets, its own
  wallpaper, its own dimming and its own labels. `/desktops/<id>` is a link
  straight to one.

- **Files: browse the folders you share with Vela.** A new app in the Launchpad
  shows the folders you name in **Settings → Files** — and only those. Make
  folders, rename things, drop files in to upload them, preview pictures, PDFs,
  sound, video and text, and switch between a list and a grid. A share can be
  marked read-only, so you can look without being able to change anything.
  Deleting moves things to a trash Vela clears after 30 days rather than
  removing them, and every change is written to Vela's audit log. Vela starts
  with a Downloads folder of its own; its own data folder can never be shared,
  and nothing outside a share is ever served.
- **Give your server a name, and yourself one.** Vela now asks what to call you
  and what to call this computer, once, when you first open it — both optional,
  both changeable in **Settings → General**. Your initial sits at the bottom of
  the rail and opens straight to those settings, the server's name heads the
  Launchpad on a phone, and Ask greets you by name. The server name is a label
  only: it does not change the address Vela answers on.
- **Deal with what needs you, or put it off.** A row in **Needs you** now shows
  what the app offered to do about it, and a **Later** that puts that one thing
  aside for eight hours — it leaves the list and the dot on the rail goes with
  it. Nothing is dismissed and the app is never told: open it and it shows
  exactly what it did before. Whatever you put off comes back on its own.
- **The desk tells you how this server is doing, in one line.** Along the bottom
  of the board: how many apps are running, how many automations ran today,
  anything that needs you, the room left where your data is kept, whether Vela
  is reachable only from this computer or from your network, and how much it has
  sent and received today. On a phone the same line sits at the top of the
  Launchpad. Vela now keeps a daily total of what this computer moves over the
  network, on this computer, for thirty days.
- **The weather, if you want it.** **Personalise → Show the weather** takes the
  name of a town, looks it up once and puts the temperature on your clock
  widget. It is off until you turn it on, and the panel says plainly that this
  is the only thing on your desk that leaves this computer: Vela asks Open-Meteo
  for a temperature at most four times an hour and sends nothing else — no
  account, no identifier, and nothing about your apps. Your coordinates are
  rounded before they are sent, and the place name is never stored.
- **The Launchpad sorts your apps four ways.** **All** is the grid you know;
  **Frequent** is the twelve you actually open, ranked over the last month;
  **Running** is what is open now; **Updates** is what has a newer version, with
  a count on the tab. Frequent stays hidden until you have opened a few things,
  because a ranking built on three clicks is not a habit. The counting happens
  on this computer, one number per app per day, thirty days kept, gone when you
  remove the app — and it is never sent anywhere. The search field now says how
  many apps it is filtering.
- **Apps can put a count on their own icon.** An app that has three things
  waiting can say so, and the number appears on its tile in the Launchpad. Vela
  does the same for itself: the Marketplace tile shows how many updates are
  waiting and the Automations tile how many runs failed today.
- **Drag an app onto your desk to make a widget of it.** Pull an app out of the
  **Your apps** widget, drop it on an empty part of the board, and that app's
  widget appears where you dropped it with arranging already open. An app that
  has no widget says so instead.
- **A refreshed Vela mark and palette.** The sail is redrawn — flatter, with
  more of the violet and cyan it always meant to have — and every place it
  appears now comes from one source file, so the browser tab, the installed app
  icon, the Windows tray and the documentation cannot drift apart again. The
  accent throughout the dashboard moves to Vela Violet, the dark theme is built
  on Deep Navy rather than a near-black grey, and running things are marked in
  Bright Cyan. Every colour Vela writes text in was checked against the surface
  behind it. If you installed Vela to your home screen or taskbar, the icon
  updates the next time it refreshes.
- **Eight painted wallpapers, and a new one each day if you like.** Personalise
  now offers Choroní, Páramo, Médanos, Chigüire, Pueblo, Ávila, Castillo and
  Canaima — places drawn for Vela rather than stock photography — each shown as
  a thumbnail you can pick by looking at it. **Daily** rotates through them,
  moving to the next picture at midnight. Choroní is what a new desk opens on.
  The Sage and Night gradients and your own uploaded image are unchanged, and
  Vela nudges the shading behind your widgets to suit a bright or a dark
  picture so the text over it stays readable either way.
- **Keyboard shortcuts and phone gestures.** Press **?** (or **Ctrl+/**) for the
  full list: **Ctrl+K** searches, **Ctrl+Space** opens or closes the Launchpad,
  **Ctrl+1**–**Ctrl+9** open the pinned apps in rail order, and **Esc** backs
  out. On a phone, swipe up from the bottom of the desk to open the Launchpad and
  swipe down from its top to close it.
- **Vela installs its own updates.** When a newer release exists,
  **Settings → Updates → Update now** downloads it, checks it against the
  checksum published beside it, backs Vela up, and reopens as the new version —
  usually in under a minute, with the page reconnecting on its own. Your apps
  and everything they saved are never part of an update. You can also let it
  install automatically at an hour you choose, which it does only when no app
  is open, no automation is running and no health check is failing. Vela keeps
  the version it replaced, so **Go back to the previous version** is there if
  you need it, and if an update stops before it finishes Vela says so the next
  time it starts and leaves you on the version you were on. Source checkouts
  and containers are pointed at `git pull` and a new image tag instead.

- **Vela tells you when there is a new version.** **Settings → Updates** shows
  what you are running, what the newest release is and what changed in it,
  rendered from the release notes. Once a day Vela asks GitHub which release is
  newest — one anonymous request that carries no identifier, no version report
  and nothing about your apps or your data. It is on by default, the section
  says so in those words, and the switch beside it stops the request entirely.
  A new release appears in **Needs you** on your desk, sends one notification
  if you have set them up, and adds an entry to the Windows tray. Installing an
  update from here comes next; for now Vela links to the right download for how
  this copy was installed.

- **Backups can run on a schedule, and you can restore one.**
  **Settings → Backups & storage** now opens with what matters at a glance —
  when the last backup was, when the next one is, how many are kept — and can
  run one a day at a time you choose. Automatic backups are off until you turn
  them on. **Restore** puts a backup back: Vela checks that it reads correctly,
  stops any running app, saves a copy of what it is about to replace, swaps the
  files, and starts the apps again. That copy is listed with your backups and
  marked *taken before a restore*, so restoring the wrong one is recoverable.
  Because a restore replaces live data, it asks you to type the backup's name.
  Your logs, wallpapers and chats are never touched by a restore, and the desk's
  Backups widget now shows when the next one is due.

- **Failures are recorded so you can look at them.** **System → Errors** lists
  what has gone wrong on this computer — errors from the engine and errors the
  dashboard catches in your browser. One failure that keeps happening is one
  row with a count, not a hundred rows; open a row for its details, resolve it
  when you have dealt with it, or delete it. A page that fails while drawing
  now shows an explanation and a Reload button instead of going blank, and the
  rail stays usable. Vela keeps the last 500 errors or thirty days, whichever
  comes first, and sends none of it anywhere.
- **Create a support bundle in one click.** **System → Overview → Create
  support bundle** writes a single file describing this server — its version
  and platform, your settings with every password and token replaced, the last
  health check, which apps are installed, your desk layout, recent errors and
  the tail of each log. It leaves out everything your apps saved, your chats,
  your wallpapers and your Vela password. Vela writes it to this computer and
  sends it nowhere; attaching it to a message is your decision. Bundles are
  deleted after a week.

- **Vela checks its own health.** **Settings → Health** runs thirteen checks
  over this computer — room left where your data is kept, whether Vela is
  answering on its address, a certificate about to expire, an app it wrongly
  believes is running, an installed folder that lost its app description, the
  runtimes automations need, how long since the last backup, and settings files
  that will no longer read — and says what each one found in plain words.
  Checks that do not apply to your setup are skipped rather than shown as
  problems. Three findings offer a **Repair**: clearing a stale app record,
  removing an empty installed folder after backing Vela up first, and putting
  back the last readable copy of a settings, desk or app-state file. Vela
  sweeps daily and shortly after it starts; a check that fails appears in
  **Needs you**, puts a dot on Settings, and sends one notification — not one a
  day. A **Health** widget shows the same summary on your desk. Nothing is sent
  anywhere: every check reads this computer.

- **Read the server's logs from the dashboard.** **System → Logs** shows the
  logs Vela writes on this computer — its own server log, a record of the
  actions taken on the server, and one log per app that runs as its own
  process — so you no longer need a file manager or a terminal to read them.
  Pick a log to see its newest lines, search it (wrap the text in slashes for a
  pattern), show 50 to 1000 lines, follow it live with auto-refresh, download
  it to share, or clear one that has grown noisy after a confirmation. `/`
  jumps to the search box and `End` to the newest line. System is now split
  into Overview and Logs tabs; both stay behind the developer-tools switch.
- **A record of what was done to this server.** Installing, launching,
  stopping, backing up, restoring and repairing are written to an activity log
  with whether they were done from this computer or over Wi-Fi.

- **A full-screen Launchpad.** Opening **Apps** now fills the screen with every
  app over your blurred wallpaper, not a side drawer. A search in the middle
  filters as you type and Enter opens the first match; **Ctrl+Space**
  (**Cmd+Space** on a Mac) opens or closes it from anywhere and Escape steps
  back. Apps that are running come first, then everything installed, then
  Vela's own tools, and a last tile leads to the Marketplace. Right-click a tile —
  or press and hold it on a phone — to open it, add its widget to your desk,
  reach its settings, stop it or remove it.

### Removed

- **The stock lake photograph no longer ships.** It had no licence anyone could
  point to. Desks still set to it open on Choroní instead; nothing you chose
  yourself is affected, and an uploaded wallpaper is untouched.
- **The All apps drawer is gone.** Its job — every app in one place — is the
  Launchpad now, so there is one answer to "which apps do I have" instead of a
  drawer, a Manage apps page and the Library all showing the same set.

## 0.1.10 - 2026-09-16

### Added

- **Apps can put a summary on your desk.** An app may declare up to four widgets
  and publish a short summary for each — a number, a few rows, or how far
  something has got. Vela draws them itself, always labelled with the app they
  came from, and nothing the app sends can run. The permission is asked for at
  install as "Show summaries on your desk" and names the widgets; refuse it and
  the app works as before. A summary that is out of date says when it was from,
  one that has not arrived yet offers to open the app, and an app that says
  something needs you raises a dot on its rail icon. Uninstalling an app removes
  its summaries. Notes and Health are the first two apps to use this.
- **Make the desk yours.** **Personalise**, above the board, sets the wallpaper
  — one of three that ship with Vela, or your own JPEG, PNG or WebP up to 8 MB,
  kept on your own computer — and whether the wallpaper is dimmed, whether app
  names show under their icons, and whether Ask sits on the board you are
  looking at. On a phone, press and hold an empty part of the wallpaper to open
  it. The phone board now opens on the time, anything that needs you, your apps
  as an icon grid, and Ask.
- **Arrange your desk.** Add widgets from a grouped list, move and resize them
  by dragging or with the arrow keys, duplicate or remove them from each
  widget's own menu, and undo or redo while you work. **Done** saves the board
  to your server, so it is the same after a restart and in another browser;
  **Cancel** puts it back, and leaving with unsaved changes asks first. The
  desktop and phone boards are saved separately and never rearrange each other.
  On a phone, press and hold a widget to start arranging.
- **Desk widgets for your server.** The desk can now show **System** (uptime,
  CPU over the last few minutes and memory use), **Volume** (how full a folder
  you choose is, and how much is free), **Flows** (automation runs today,
  failures and the average run) and **Backups** (when Vela last backed itself
  up, with **Back up now**). Add the folders you want to watch under
  Settings › Desk; Vela only reports how full they are. A server that cannot
  read its own CPU and memory says so instead of showing zeros.

### Changed

- **The rail shows what is open, and All apps holds the rest.** Apps the server
  is running now sit together at the top of the rail under an OPEN label, above
  your other apps. A new **All apps** control opens every installed app over
  whatever page you are on, with a search field, what is running first and each
  app's version, and one way to add another. Manage apps has a different icon so
  the rail never shows the same one twice.
- **Vela opens on a desk.** The home screen is now a board of widgets over a
  wallpaper instead of a plain app launcher: your apps, the time, what is
  running and your latest Ask conversation, with the rail beside it. Widgets
  only show what Vela actually knows, so nothing on the board is a placeholder.
  The desktop board is six columns wide and the phone board two, each laid out
  and saved on its own, and both are kept on your server so they are the same
  after a restart and in another browser.

## 0.1.9 - 2026-09-15

### Changed

- **The app rail stays on screen on a phone.** Every dashboard page, including
  an open app, now keeps the rail of destinations and installed apps beside its
  content at phone widths, so switching apps or pages is one tap away. The
  menu button that used to open the rail in a drawer is gone from page headers;
  on Ask, the header button now opens only the conversation list.
- **Apps open inside the dashboard.** An app that did not ask for a seamless
  view now renders beside the rail with its name and **App settings** in the
  dashboard's own header, instead of drawing a second bar with Back and Close
  above it. Seamless apps are unchanged, and the Vela menu's **Show compact
  bar** now shows that same dashboard header, with **Hide app bar** in it to
  return.

## 0.1.8 - 2026-09-15

### Added

- **App lock.** A phone or remote browser signed in to Vela can now be locked
  behind a six-digit PIN or a nine-dot pattern, so a glance at your unlocked
  phone does not show your apps. Set it up under Settings → Security: Vela asks
  for your account password, then for the new PIN or pattern twice. Choose how
  soon it locks on its own — 1, 5 or 15 minutes of no activity, 5 by default —
  or lock it immediately with Lock now. Changing the method, changing the timer
  and turning it off each ask for your Vela password again, and your password
  always works on the lock screen. After five wrong tries only the password is
  accepted, and that limit follows the session rather than the tab, so reloading
  the page, opening another tab or changing network does not reset it. The
  server enforces the lock, not the page: a locked session's apps, data,
  automations and open chat streams all stop answering until it is unlocked,
  including app connections that were opened before it locked. Background
  updates do not count as activity, so a phone left on a table still locks on
  time. Nothing about the lock is stored in the browser, and the PIN or pattern
  itself is never written to disk. App lock covers the browser session it was
  set up in; it does not encrypt files on your Vela computer, does not lock that
  computer's screen, and the Vela computer's own dashboard, which opens without
  signing in, is unchanged.

### Changed

- **Settings is a phone screen on a phone.** Below the shared compact width it
  fills the screen instead of floating in a popup, and opens on a searchable
  list of categories with a short line saying what each one holds. Tapping one
  opens that category on its own, with Back in the header; Back, Escape and the
  phone's own back gesture all step through the same screens you can see. Rows
  and controls are sized for a finger, form text stays readable, and the layout
  keeps clear of the notch and the home indicator. A draft you were part way
  through keeps its place while you move between categories, turn the phone, or
  cross between the phone and desktop layouts. On a wider screen Settings is the
  same two-pane popup it was, and existing links such as `/settings#backups`
  still open their section directly.
- The navigation rail's logo and destinations sit lower, with real space above
  them instead of being pinned to the corner, in the desktop rail and the phone
  navigation drawer alike. A short window gives that space back so the controls
  at the bottom stay reachable.
- An app that asks for Vela's own workspace chrome now keeps the app rail on
  screen on a phone as well as on a desktop, so switching apps, going Home and
  opening Settings stay one tap away while the app changes its own panes
  underneath. The rail takes its own narrow column rather than covering the app,
  and the app's description is left to the rail and header instead of repeating.
  Apps that present themselves with a compact bar or full screen are unchanged.

## 0.1.7 - 2026-09-15

No additional release notes were provided.

## 0.1.6 - 2026-09-15

No additional release notes were provided.

## 0.1.5 - 2026-09-15

No additional release notes were provided.

## 0.1.4 - 2026-09-15

No additional release notes were provided.

## 0.1.3 - 2026-09-15

No additional release notes were provided.

## 0.1.2 - 2026-09-14

No additional release notes were provided.

## 0.1.1 - 2026-09-14

### Added

- **Custom bots and shared rooms** in Ask. Create a bot with its own name,
  instructions and model, chat with it, and bring two to four of them into a
  room to work on something together. Vela can draft a bot's instructions from a
  plain description, and you can try a bot out before saving it — the preview is
  never written down. A new bot can read nothing about this hub; the editor can
  allow any of the four read-only things the built-in assistant already does, and
  Vela re-checks that permission at the moment a bot uses it rather than trusting
  its instructions. Rooms answer either by mention, where only the bots you
  `@mention` reply and the lead answers otherwise, or as a roundtable where every
  bot replies once in order and sees what was said before it. Each bot answers
  exactly once per message, a bot writing `@someone` never summons anyone, Stop
  cancels the whole run including bots that had not started, and a bot that fails
  can be retried on its own without repeating replies that already worked. The
  `@` picker now lists bots and apps as separate, labelled groups, and app
  mentions keep their existing meaning. Existing conversations keep their
  addresses, history, drafts and archive state, and become direct chats with the
  built-in Vela assistant; their old answers are attributed to Vela without
  claiming a model that was never recorded. See
  [the bots and rooms guide](docs/BOTS.md).
- **Automations** is now a working feature instead of a preview. Build an
  automation visually from a trigger and a set of steps, and Vela runs it on the
  server computer, including while the browser is closed. Triggers are Run
  manually, a repeating schedule in a timezone you choose, or an authenticated
  web request. Steps cover building text, branching on one field, waiting,
  writing to the run log, sending a notification, waiting for your approval, and
  asking an installed app to perform an action it offers. Saved automations,
  their versions, permissions, schedules and run history survive a restart, and
  every run records what each step did and how long it took. A run that Vela
  could not finish says so rather than reporting success: cancelling stops the
  steps that had not started and never claims to undo the ones that had.
  Automations do not run while Vela is off — scheduled times that pass are
  skipped and reported, not replayed as a burst. See
  [the automations guide](docs/AUTOMATIONS.md).
- An automation that wants to change an installed app's data asks first. Vela
  shows the exact app, action and inputs, and the permission belongs to that one
  automation and that app as it is installed now. Editing the step, adding
  another step that calls it, updating the app or reinstalling it ends the
  permission and Vela asks again; removing it stops the next call, including in a
  run already under way. Repeating a step inside one run reuses its result rather
  than writing twice, while running the automation again is a new write. Existing
  app-to-app permissions are unchanged.
- Vela server downloads include the engine automations run in, so there is
  nothing extra to install. It adds roughly 30 MB to a download. A build without
  it still installs and runs; its automations page explains what is missing
  instead of failing a run halfway. Contributors building from source run
  `python scripts/setup-automation-worker.py` once, and
  `python scripts/fetch-node-runtime.py` before building a download; see
  [the developer guide](docs/DEVELOPMENT.md#automations). Update Python
  requirements for the timezone database that schedules need.
- ServerKit install badge, deployment manifest and container build with
  persistent app data and password-protected access through a trusted HTTPS
  proxy. Set the public origin and proxy address before the first deployment.
- Connect existing HTTPS web services from **Library → Add an app → Connect a
  web service** with a name, address and icon color. They open beside the rail
  with reload, edit and browser fallback controls in the bar above them.
  Connections use a separate hostname and keep the service's own login and data;
  removing a connection does not stop or uninstall the service.
- The welcome popup connects iPhone and Android users with a QR code. Enable
  password-protected Wi-Fi access directly on the server computer; the phone
  page guides certificate trust, browser choice and Home Screen installation.
  Hosted servers use their configured HTTPS domain. Reopen setup or turn off
  Wi-Fi access from Settings. Contributors should update Python requirements
  and run `npm --prefix web ci` for certificate and QR-code dependencies.
- Windows installer with the Vela sail icon, Start menu integration and optional
  startup at sign-in. Windows portable downloads remain available.
- Windows tray controls for server status, opening the dashboard, starting and
  stopping the server, sign-in startup and logs, without keeping a terminal open.
- App authors can ask Vela to draw the navigation and the app's name around their
  app with `"view": {"chrome": "hub"}`, and match its appearance with the
  `vela-app.css` and `vela-theme.js` files that now ship in every generated app.
  `compact` and `seamless` apps are unchanged until their manifest opts in.
  See [the developer guide](docs/DEVELOPMENT.md).

### Changed

- **Vela lays out for a phone properly.** Tapping a field no longer zooms the
  page and leaves it zoomed: every field a finger can reach now renders at 16
  pixels or more, while larger text you have chosen is kept. When the keyboard
  opens, the composer, a form dialog's buttons and a drawer's actions stay above
  it — the list or conversation above gives up the space, and your place in what
  you were reading is kept rather than jumping. Dialogs and drawers scroll their
  own body while the page behind them stays where you left it, and reaching the
  end of a list no longer starts moving the page underneath. Pinch zoom, panning,
  text selection, copy and paste and the browser's Back gesture all keep working,
  and zooming in is no longer mistaken for the keyboard opening, so the layout
  does not rearrange itself while you magnify it. Grids no longer push the page
  sideways at 200% zoom or on a narrow phone, and long addresses and identifiers
  wrap instead of widening everything around them. Home keeps its navigation rail
  and one-tap app shortcuts at every size, including narrow phones, short
  landscape windows and tablet split screen. Contributors: one viewport service
  now owns measurement, publishes it as CSS variables, and each safe area and
  keyboard adjustment has a single named owner, so nothing subtracts the same
  space twice; see [the developer guide](docs/DEVELOPMENT.md).
- Apps are told what part of their frame is actually on screen. The viewport
  insets Vela reports to an app are now measured in the app's own frame instead
  of the host's coordinates, so an app that respects them protects the right
  edges and never subtracts a keyboard Vela has already taken out of the frame.
  A frame Vela has already sized to the visible area reports no insets at all.
  The bridge protocol, the manifest and the existing fields are unchanged; the
  Vela starter stylesheet in `vela-templates` and `create-vela-app` adopts them
  through a new `vela-viewport.js`, and the Notes app uses it.
- App icons now tell apps apart. Each app draws its own mark — a notepad for
  Notes, a fork and knife for Meals, a heart and pulse for Health, a chip for
  System Info — instead of every app sharing one white glyph on the same
  coloured gradient. The tile is a quiet tint of the app's own colour rather
  than a saturated one, and a connected web service shows its initial instead of
  another globe, so a list of services can be read at a glance. Apps keep the
  colour they declare, and tiles are evened out so a pale colour carries the
  same weight as a deep one. The marks stay legible down to the small size the
  app rail and search results use, in both themes, and nothing is fetched while
  the page runs, so icons look the same offline.
- Vela now keeps its technical side out of the way until you ask for it. A new
  **Settings → General → Show developer tools** switch, off to begin with, adds
  app logs, process details, runtime commands and ports, execution history,
  manual start and stop controls, the **System** overview and the server-folder
  install source. It changes only what that browser shows: it never alters an
  app's permissions, starts or stops anything, or changes what Vela allows, and
  it is remembered per browser, so a phone does not inherit a desktop choice.
  Switching it takes effect at once, without reloading the page, closing an open
  app or discarding an unsent message. Links you already have to `/environments`
  and the technical settings keep working: they explain what they are and offer
  to turn the switch on, and visiting one never turns it on by itself.
- Managing an app moved from above it into **App settings**, reached from the
  app's own bar or Vela menu: its permissions, its connection, importing earlier
  data, updating it and removing it. Everyday use starts with the app itself,
  not a stack of panels. Permission requests stay in plain view without
  developer tools — a waiting decision is announced above the app with a
  **Review** control, and the review names the app asking, the action, the app it
  happens in, and what Allow does and does not give away. **Stop allowing**
  revokes it. The technical record of what has run is now a per-app diagnostic,
  collapsed and loaded only when you open it. Required setup, data migrations,
  failed updates and outages are still shown whether or not developer tools are
  on.
- Home is now the app launcher and nothing else: your installed apps, each
  described in its own words, and one **Add an app**. Choosing an app opens it,
  and opening a stopped app starts it once and says so while it does. The server
  version, the installed and running counts, the system widgets and the second
  catalog banner are gone from it; the facts they carried are in Settings and
  System, and nothing invented has taken their place. An empty server says
  “Add your first app”. Library still shows catalog cards with each app's
  description, category, version and the one action that applies to it, category
  filters with counts, a filter for pending updates, and a single Add an app
  dialog covering the supported sources: **Install from file**, **Connect a
  website**, and a folder on the computer running Vela while developer tools are
  on.
- Ask keeps your conversations. A panel beside the conversation lists them by
  date with search, rename, archive and permanent delete, the open conversation
  is part of the address so reloading or sharing the link returns to it, and each
  conversation keeps its own unsent draft. Follow-up questions use that stored
  conversation, so restarting the server no longer answers as if the history were
  not there, and a stopped answer is kept with the question it belongs to.
  Starting another conversation never erases the previous one. The transcript
  your browser used to hold is imported once, and turning chat history off in
  Settings still deletes everything that was stored. On phones the conversation
  list opens in the navigation drawer beside the rail, the header keeps only the
  drawer control, the title and New, and the composer is a single rounded field,
  so the chat fills the screen.
- The dashboard now uses a narrow app rail instead of a labelled sidebar. The
  rail keeps Home, Ask, your installed apps and Library one click away, with
  Automations and Manage apps named in a **More** menu beside them and Settings
  at the foot. It marks the open destination and names each icon on hover and
  keyboard focus. Search and notifications moved into a contextual header above
  each page. On phones the bottom bar is gone: Home keeps the rail on screen
  beside its content, so a ready app is one tap away with no menu step, and
  every other page's header navigation control slides the same rail in from the
  left edge beside a labelled list of every destination. Content and app
  workspaces now use the full height of the screen.
- Apps that ask for the hub's own layout, and connected web services, now open
  beside the rail with a single contextual header showing the app's icon, name
  and state instead of a second app bar. Connected services keep their edit,
  reload and open-in-browser controls in that header, and their own origin,
  login and data are untouched. Apps that declare the compact or seamless
  presentation are unchanged until their manifest opts in. Opening an app that
  has been removed now lands in the normal workspace with a way back.
- Settings opens in a compact popup over the current screen, with searchable
  categories, light and dark previews, and separate chat privacy controls. The
  categories are now General, Appearance, Chat & privacy, Local AI,
  Notifications and Backups & storage, plus Developer tools once that is
  switched on. Existing settings links still open a matching category — the
  storage link lands in Backups & storage, and the network and app-environment
  links in Developer tools. Phone layouts keep the categories and controls
  inside the popup.
- Ask now keeps its multiline composer at the bottom, with a separately scrolling
  conversation, formatted replies, copy controls, expandable checks, and a jump
  to the latest response. Type `@` to find an installed app by name or ID and
  mention it in a question. Stopped or interrupted answers remain visible and
  can be retried. Contributors should run `npm --prefix web ci` for the new
  Markdown dependencies.
- Contributors can run `npm --prefix web run check` for lint, formatting, tests
  and the dashboard build; CI uses the same command. The dashboard now shares
  engine status across pages and reuses dialogs/drawers with keyboard focus
  handling and dismissal guards while work is pending. Development requires
  Node.js 22.13+ in the 22.x line or Node.js 24+; run `npm --prefix web ci` after
  updating to install the new check tools.
- Dashboard development now uses organized SCSS modules, shared UI controls and
  request hooks, and one route/navigation definition. The developer guide
  includes an example for adding a page; contributors should run `npm --prefix
web ci` to install the Sass build dependency.
- Automatic releases now require the tested Windows installer alongside all
  three portable downloads and checksums. Windows upgrades and uninstall keep
  existing apps and data; installers and executables carry Vela branding.

### Removed

- The "What the assistant can see" panel above Ask conversations. What the
  assistant can reach is unchanged: installed apps, their recent logs and the
  engine status, never the data inside your apps.

### Fixed

- Installing or updating an app no longer fails on Windows when a virus scanner
  still holds a handle on the files that were just written. The move into place
  retries briefly before reporting a permission error.
- Long dashboard pages scroll independently of the desktop navigation, which
  stays in place. A server that is answering no longer reports itself in every
  header; a connection that has actually failed says “Can't connect to Vela”
  and offers Retry, and data that is merely missing or still loading is treated
  as neither success nor an outage.
- Browser tabs and Home Screen installs now show Vela's purple-and-cyan sail
  logo with its transparent corners instead of the old amber icon. After updating
  the server, remove and re-add an existing Home Screen shortcut if it still shows
  the old icon.
- The dashboard Ask page can talk to the local model again. Sending a message
  failed immediately with "Request failed (422)" because the dashboard posted a
  request shape the server rejected. Failed requests now also show the server's
  explanation instead of only a status code.

## 0.1.0 - 2026-09-14

### Added

- Personal app server with a browser dashboard, local app lifecycle management,
  settings, notifications, backups and home-screen installation support.
- Versioned app manifests, a scoped browser SDK, persistent app data with
  revision checks, legacy data import and explicit app-to-app action grants.
- Reviewed app folder/ZIP installation, pinned release catalogs, independent
  app updates and rollback to matching package/data checkpoints.
- Password-protected HTTPS access for other devices and a scoped connection
  to an existing Ollama server.
- Portable server packaging that includes the Python runtime and built
  dashboard, opens the browser after startup, and supports headless launch.
- A workflow for building and smoke-testing native server downloads.
- Contributor and security guides, maintainer credits, donation links and QR
  codes, and shared agent instructions requiring meaningful changelog updates.
- Pull request templates and Windows/Linux checks for the hub and dashboard.

### Changed

- Merging `dev` into `main` now versions, builds and smoke-tests Windows,
  macOS and Linux downloads, then publishes a GitHub Release with the archives
  and SHA-256 files attached. Manual runs on `main` use the same release flow.
- Separated apps, SDK, contracts, templates, generator and catalog into their
  own repositories. The hub runs and tests without sibling checkouts.
- Simplified the Quick Start around downloading and opening Vela Server;
  source setup and packaging commands now live in the developer guide.
- Replaced public implementation-increment notes with current app/testing
  guides. Working plans and historical notes are kept locally and excluded
  from GitHub.
