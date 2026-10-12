# Hosting

Two supported targets: cPanel's **Setup Python App** (steps 1–8, and the source
of `amuaa-cpanel.zip`) and Render or any similar PaaS that starts the app with a
shell command (step 9).

The app is a plain Flask 3 program with an SQLite database. cPanel's
**Setup Python App** runs it through Apache + Phusion Passenger, so there is no
gunicorn or Nginx step.

## Running it on your own machine

Debug mode is opt-in, so a plain `py run.py` looks like an unconfigured host and stops with
the `SECRET_KEY is required` message. Ask for debug instead:

```
cmd>        set FLASK_DEBUG=1 && py run.py
PowerShell> $env:FLASK_DEBUG=1; py run.py
```

That gives you the reloader, a throwaway session key, and the starter credentials on startup.
To keep the demo data away from a database you care about, add
`set DB_PATH=demo-ui.db` (or `:memory:`) before `py run.py`, and `set PORT=5100` if 5000 is
taken on your machine.

## 1. Where the files go

Put the app **outside** `public_html`, in its own folder:

```
~/amuaa-app/          <- the app (run.py, templates/, static/, assets/, uploads/)
~/public_html/        <- stays empty, or keeps only your old static pages
```

Passenger reads the app from wherever you point it in step 3, so nothing in the
app folder needs to be web-readable. The `.htaccess` in the zip is only a safety
net for the case where the folder is accidentally placed inside `public_html`.

Unzip the archive, then in cPanel: **Files → Permissions** on `uploads/` and the
app folder itself: `755` (the SQLite file also needs the *folder* writable, because
SQLite creates `database.db-wal` and `database.db-shm` next to it).

## 2. Create the virtual environment

cPanel → **Python Apps → Create Application**:

| Field | Value |
| --- | --- |
| Python version | 3.10 or newer (the app is built and tested on 3.14) |
| Application root | `amuaa-app` |
| Application URL | your domain |
| Application startup file | `passenger_wsgi.py` |
| Passenger app log | `logs/app_error.log` |

Open the terminal cPanel offers for the app (or run the equivalent by hand) and
install the dependencies:

```
source ~/venv/bin/activate      # the venv path cPanel shows you
pip install -r requirements.txt
```

Then press **Restart** on the application page. Passenger imports
`passenger_wsgi.py`, which exposes `application` from `run.py`.

## 3. Environment variables — set these before the first visit

In the same screen, **Add Environment Variable**. Anything you leave unset falls
back to a development default, which is why `SECRET_KEY` is mandatory: outside
debug mode the app refuses to start without it.

| Variable | Needed | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | **required** | Signs session cookies. Generate: `python -c "import secrets; print(secrets.token_hex(32))"`. Changing it logs everyone out. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | first boot only | The admin login created when the database is empty. Defaults are `admin` / `admin123`, and the startup log warns while any account still accepts them — change it from the admin panel. |
| `DB_PATH` | optional | Absolute path to the SQLite file. Default: `database.db` inside the app folder. Set it to a path outside the app if you keep data elsewhere. |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD` | to send mail | Defaults to `smtp.gmail.com:587` with STARTTLS. Use an app password, not the account password. |
| `MAIL_USE_SSL` / `MAIL_USE_TLS` | optional | `1` / `0`. TLS on 587 is the default; SSL on 465 needs `MAIL_USE_SSL=1`, `MAIL_USE_TLS=0`. |
| `MAIL_DEFAULT_SENDER` | optional | Defaults to `MAIL_USERNAME`. |
| `ADMIN_NOTIFY_EMAILS` | optional | Comma-separated addresses that receive new-application notices. |
| `SESSION_COOKIE_SECURE` | optional | Defaults to on in production. Set `0` **only** if you are testing over plain HTTP, otherwise the session cookie is dropped and nobody can log in. |
| `FLASK_DEBUG` | never on a host | `1` turns on the reloader and the interactive debugger, which executes code. Debug is off unless you ask for it. |

## 4. HTTPS

Enable **Force HTTPS** (cPanel → Domains) before you tell anyone the login URL.
Cookies are marked `Secure` in production, so over plain HTTP a login will not
stick — that is deliberate.

## 5. First run

Visit `https://yourdomain/` — the public page with the live course list, then
`/admin/login` with the credentials from `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

An empty app folder has **no database file on purpose**: when the app starts it
runs `init_db()`, which creates the schema and seeds the departments, courses and
the admin account. To carry over data from an existing installation, upload
`database.db` (plus `-wal` / `-shm` if they exist) into the app folder **before**
starting the app, then restart it.

## 6. Backups

The whole state is two things: `database.db` and `uploads/`. Add both to cPanel →
**Backup** (or a nightly cron `cp` to a folder outside the app). Copying the
`.db` while the app is running is safe with WAL, but the simplest reliable habit
is to press Restart, copy, and restart again.

## 7. Deploying a change

Upload the changed files over the old ones, then **Restart** the application.
Restarting is also what makes Passenger pick up an edited `requirements.txt`.

## 8. When it does not come up

Passenger shows one generic error page, so the answer is always in the log you
chose in step 2 (`logs/app_error.log`):

- `SECRET_KEY is required to run outside debug mode` — the variable is missing.
- `ModuleNotFoundError: No module named 'flask'` — `pip install -r
  requirements.txt` ran outside the app's virtualenv, or the wrong Python version
  was selected.
- `Application starting up failed` with a traceback above it — the traceback is
  the real cause; `passenger_wsgi.py` prints it to the log before re-raising.
- Login page loads but the session drops you back at the login form — HTTPS is not
  on, or `SESSION_COOKIE_SECURE` was set while serving over HTTP.
- `attempt to write a readonly database` — the app **folder** needs write
  permission, not just the `.db` file.

## 9. Deploying on Render (or any `gunicorn`-style PaaS)

Render builds from the Git repo, installs `requirements.txt`, then runs a shell
**Start Command**. Its Flask template ships the placeholder
`gunicorn your_application.wsgi`, which fails twice over: there is no module
called `your_application` here, and gunicorn is not installed. Both are fixed on
the service settings:

| Setting | Value |
| --- | --- |
| Build command | `pip install -r requirements.txt` (Render runs this automatically when it detects the file) |
| Start command | `gunicorn --bind 0.0.0.0:$PORT --timeout 120 run:app` |
| Environment | `SECRET_KEY` (required), `ADMIN_USERNAME`, `ADMIN_PASSWORD`, plus any `MAIL_*` |

`run:app` is the WSGI object — `run.py` builds it at import time with
`app = create_app()`. The `Procfile` in the repo carries the same command, but a
Start Command typed in the dashboard wins, so set it in both or just in the
dashboard. Binding to `0.0.0.0:$PORT` is not optional: gunicorn defaults to
`127.0.0.1:8000`, and Render routes traffic to the port it advertises in `PORT`,
so anything else deploys "successfully" and then returns 502.

**The data will not survive.** Render's filesystem is reset on every deploy and
restart, so `database.db` and everything under `uploads/` (photos, documents,
exam papers) revert to whatever was committed to Git. To keep real applications
there you need a **Disk** attached to the service (paid instance types only),
mounted at a path such as `/var/data`, with:

```
DB_PATH=/var/data/database.db
```

`uploads/` has no environment override — it is always the folder beside the app —
so on Render either accept that uploaded files are temporary, or mount the Disk
at the app's `uploads` path. If you would rather not pay for a Disk, cPanel
(steps 1–8 above) keeps the same SQLite file on ordinary hosting.

Also note that Render's default Python may be older than the one you develop
with. The app needs 3.10 or newer; if the build log shows a `SyntaxError`, set
`PYTHON_VERSION` (for example `3.12`) in the environment and redeploy.

