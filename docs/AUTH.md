# Accounts and per-user data

RecallAI has user accounts. Each account only sees, asks about and gets alerts
for **its own** belongings, sightings, alerts and people. One camera can track
everyone's belongings at once: each sighting is tagged with the owner of the
belonging it matched.

## The workflow

1. **Create an account** in either dashboard (http://localhost:5173 or
   http://localhost:5174 / http://localhost:8000/app/), or on the GPS / search
   pages served by the server. Username 3–32 characters (`a-z 0-9 . _ -`,
   case-insensitive), password at least 8 characters, optional display name.
   Registering signs you in.
2. **Register a belonging.** In the camera dashboard press **Add** and show the
   object to the camera: the new belonging is yours (`"owner_id"` in its
   `data/belongings/<id>/info.json`). From the command line:
   `python -m recallai.vision.register --user <username> --name "My Keys" --category remote`.
3. **The camera recognizes it** (`python -m recallai.vision.webcam`). The
   camera is a trusted local device, not a user: it recognizes every account's
   belongings, and each sighting gets the matched belonging's owner (and that
   owner's phone GPS, if their phone is sharing it).
4. **Ask "where is my X?"** The search API and the Grok agent only look at your
   rows. The owner is fixed by the server for the request; it is never a tool
   argument, so the model can't ask for someone else's data, and sightings it
   cites that aren't yours are dropped.
5. **Alerts** ("did you forget your bag?") are created per owner, use that
   owner's phone GPS, and Grok reviews each owner's items in a separate request.
   `GET /api/alerts` lists only yours; dismissing someone else's alert id is a 404.
6. **Sign out** revokes the session token on the server.

## First launch: who owns the existing data?

Data recorded before accounts existed has no owner (`owner_id` NULL in
SQLite, no `owner_id` in `info.json`). **The first account ever registered
claims all of it**: every ownerless sighting, alert, person and belonging
folder. This happens in the same database transaction that creates the
account (`BEGIN IMMEDIATE`), so two simultaneous sign-ups can't both claim it.
Later accounts start empty. Nothing is ever deleted.

So on an existing install: **start the server and create your own account
first.** (Anyone who can reach the server could otherwise sign up first.)

If ownerless data appears later (e.g. `register.py` was run while no account
existed), the next first account claims it; to give it to a specific account
later: `python -m recallai.memory.users claim <username>`.
`python -m recallai.memory.users list` lists accounts.

`register.py` picks the owner like this: `--user <username>` if given; else
the only account if exactly one exists; else, with no accounts, the item is
ownerless (claimed by the first account); with several accounts it asks for
`--user`.

## Migration (memory.db)

`recallai.memory.db.init_db()` (run by every entry point) upgrades an existing
database in place and is safe to run repeatedly:

- adds `owner_id INTEGER` to `sightings`, `alerts` and `people`
  (`ALTER TABLE ... ADD COLUMN`; existing rows keep every value, owner NULL);
- creates `users` (`id`, `username` UNIQUE COLLATE NOCASE, stored lowercase,
  `display_name`, `password_hash`, `created_at`) and `sessions` (`user_id`,
  `token_hash` UNIQUE, `created_at`, `expires_at`, `revoked_at`);
- adds indexes on the owner columns and on `sightings.image_path/crop_path`
  (used to authorize snapshot downloads).

Belongings stay on disk; ownership is the `"owner_id"` key in `info.json`.
SpacetimeDB tables get an `owner` column (see
[SPACETIME_CONTRACT.md](SPACETIME_CONTRACT.md), v3); republish the module
(`spacetime publish`, no `--delete-data` needed: the column has a default).

## Endpoints

| Endpoint | Auth | Notes |
|---|---|---|
| `POST /api/auth/register` `{username, password, display_name?}` | none | 201 `{token, token_type, expires_at, user, claimed}`; 422 `{detail: {message, field}}` for invalid input; 409 if the username is taken |
| `POST /api/auth/login` `{username, password}` | none | 200 `{token, ...}`; 401 `"Incorrect username or password."` for a wrong password **and** an unknown user; 429 + `Retry-After` after 5 failures for that username within 5 minutes (60 s lockout) |
| `POST /api/auth/logout` | Bearer | revokes this token |
| `GET /api/auth/me` | Bearer | `{id, username, display_name, created_at, session_expires}` |
| `/api/belongings*`, `/api/sightings*`, `/api/objects`, `/api/people*`, `/api/ask`, `/api/alerts*`, `/api/enroll*`, `GET /api/live`, `POST /api/live/people`, `POST /api/people/{id}/describe`, `POST /gps`, `GET /gps/latest` | Bearer | only the caller's data; someone else's ids behave like missing ones (404) |
| `GET /snapshots/<file>`, `GET /api/live/frame.jpg`, `GET /api/live/stream`, `GET /api/people/{id}/photos/<file>` | Bearer **or** `?token=` | for `<img>` tags; a snapshot is only served to the owner of a sighting that references it |
| `POST /api/live`, `GET /api/device/gps` | device | the camera process (below) |
| `/`, `/search`, `/auth.js`, `/app/` | none | pages; they sign in before calling the API |

Without a valid token every user-data endpoint answers **401** with
`WWW-Authenticate: Bearer`; both dashboards then return to the sign-in screen.

### Token transport: why Bearer (plus `?token=` for images)

The token is sent as `Authorization: Bearer <token>` on every API call. A
bearer header is never attached automatically by the browser, so another
website can't make requests on the user's behalf (no CSRF), and the same
scheme works for the dev servers on :5173/:5174 (via the Vite proxy or CORS,
which allows the `Authorization` header from those origins) and for
`curl`. The one place a page can't set a header is `<img src>` (snapshots,
the live camera frame, the MJPEG stream), so only those **GET media routes**
also accept `?token=`. Trade-off: that URL can show up in the server's access
log and browser history on the same machine; the JSON APIs never accept it.

Tokens are `secrets.token_urlsafe(32)`; only their SHA-256 is stored. They
expire after 30 days and are revoked on sign-out. The browser keeps the
session in `localStorage` under `recallai.auth` (shared by the live dashboard
at `/app/` and the server's HTML pages, which have the same origin).

Passwords are hashed with `hashlib.scrypt` (n=2^14, r=8, p=1, random 16-byte
salt per user) and compared with `hmac.compare_digest`. Login with an unknown
username still runs one scrypt, so response time doesn't reveal which names
exist.

## The camera and other devices

`webcam.py` is not a user. Its endpoints (`POST /api/live` to publish the
annotated frame and enrollment progress, `GET /api/device/gps` to fetch every
user's fresh phone GPS) accept a request when either:

- it comes from **this machine** (loopback address) **without** a browser
  `Origin` header and **without** `X-Forwarded-For`. So the camera works with
  no setup when it runs next to the server, but a browser page (which always
  sends `Origin` on a POST) or a phone going through the Vite dev proxy
  (which forwards from 127.0.0.1) can't push fake frames; or
- it carries `X-RecallAI-Device-Key: <key>` matching the server's
  `RECALLAI_DEVICE_KEY` environment variable. Use this for a camera on another
  machine: set the same `RECALLAI_DEVICE_KEY` for both, and
  `RECALLAI_SERVER_URL=http://<server>:8000` for the camera.

A signed-in user's token is not a device credential.

**Phone GPS is per user.** The phone page (`http://<laptop-ip>:8000/`) asks
you to sign in, then posts your location to `POST /gps`, which stores it for
your account only (in memory, as before). The camera fetches
`{"by_owner": {"<user id>": fix}}` from `/api/device/gps` and attaches each
belonging's **owner's** fix to its sightings; the watcher's "moved away" rule
compares each item with its owner's phone, and the agent's `get_context` /
distances use the asking user's phone.

**Enrollment** ("Add" in the camera dashboard): the camera can enroll one item
at a time, owned by whoever pressed Add. The server passes that user's id to
the camera with the enroll status, the camera writes it into the new
`info.json`, and the server also tags the belonging when the camera reports
its id. Other users see "the camera is busy" (without the item's name) and
can't start or cancel that session (409).

**Live view**: the camera frame shows everything the camera sees (including
boxes drawn for other users' items); any signed-in user may view it. In
`GET /api/live` the `in_view` list (recognized belongings, by name) only
contains the caller's items; `detections` are anonymous YOLO categories.

**Naming people on the live feed**: `POST /api/live/people` `{bbox, name}` (or
`{bbox, person_id}`) names the person in that box for the caller's account,
creating the person if the name is new. The server queues the box for the
camera (returned as `people_jobs` from `POST /api/live`); the camera saves a
crop to `data/people/<person id>/` (git-ignored, deleted with the person) and
then reports recognized people as `people` in its frames. `GET /api/live`
lists only the caller's people, with their current name. Names are never
drawn on the shared frame, so other accounts don't see them.

## SpacetimeDB (live dashboard)

The bridge mirrors each row with `owner` = the owner's user id as text (`""` =
ownerless). The live dashboard subscribes with `WHERE owner = '<your id>'`
and filters again before rendering, and it only connects to SpacetimeDB after
you sign in.

**Security note:** SpacetimeDB tables in this module are `public`, and
SpacetimeDB doesn't know RecallAI accounts, so anyone who can reach
SpacetimeDB on :3000 could subscribe to every row, and `dismiss_alert` can't
check who owns an alert. That's acceptable for a local, single-machine app,
but it is filtering, not access control. Real per-user enforcement would need
SpacetimeDB row-level security (or private tables exposed through views /
procedures keyed on a SpacetimeDB identity that the server links to a RecallAI
account), and the bridge would have to stop pushing other users' rows to
shared public tables.

## Other security notes

- Run the server on a trusted network. Registration is open to anyone who can
  reach it; each new account only sees its own (initially empty) data, but the
  first account claims all pre-account data, so create yours first.
- Failed sign-ins are throttled in memory (per username, reset on restart).
- The CLI tools (`python -m recallai.ai.agent`, `register.py --list`, the
  bridge) run as the machine owner and are not scoped to an account.
- Plain HTTP: tokens travel unencrypted on the LAN (e.g. from a phone). Put the
  server behind HTTPS if that matters.
