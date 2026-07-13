# Nala Secondary School Management Portal

A real, deployable Python (Flask) web app with its own SQLite database — built to be hosted online so anyone
at your school can reach it from any device via a normal web address.

## What's included
- Student / Teacher / Admin login (separate roles, hashed passwords, lockout after 5 failed attempts)
- Student results, NECTA-style grading, division, GPA, class ranking
- Teacher score entry, class analysis, results sheets, NECTA-format documents, parent reports (PDF)
- Admin: student registration by CSV upload or paste, student search/edit/delete
- Staff management: add/remove teachers (multi-subject) and admins
- Configurable curriculum: subjects, class levels (Form I–IV+), term label, academic year
- Fees: per-class fee structure, custom per-student amounts, payments by category, PDF receipts,
  printable PDF reminder letters for unpaid balances
- Announcements, school-wide activity log
- Database backup/restore (download/upload the `.db` file directly)

## Running it locally (to try it first)
```bash
pip install -r requirements.txt
python app.py
```
Then open `http://localhost:5000` in your browser. First login: **Admin ID `ADM-001`, password `admin123`**
— change this immediately under Manage Staff once you're in.

The database (`school.db`) is created automatically on first run, in the project folder.

## Deploying it as a real website

This project is a standard Flask app, so it can go on almost any Python hosting service. The simplest options:

### Option A: Render.com (recommended, has a free tier)
1. Push this project to a GitHub repository (or use Render's "upload" option if you don't want to use Git).
2. On Render, choose **New → Web Service**, connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Once deployed, Render gives you a permanent URL like `https://nala-school.onrender.com` — that's your website.

### Option B: Railway.app / Fly.io / PythonAnywhere
All work similarly — point them at this folder, they detect `requirements.txt` and `Procfile` automatically.

### Important: persistent storage
SQLite stores data in a single file (`school.db`) on disk. Some free hosting tiers **wipe the filesystem on
every redeploy or restart**, which would erase your data. Before going live, check your host's docs for:
- A "persistent disk" or "volume" option (Render, Railway, and Fly.io all offer this — attach it and point
  `school.db` at that mounted path), **or**
- Switching to a hosted database later (e.g. Postgres) if you outgrow SQLite — the app would need a small
  adjustment for that, ask me when you're ready.

For a small school, SQLite on a persistent disk is genuinely fine — no need to overcomplicate this.

### Environment variable
Set `SECRET_KEY` to a random long string in your hosting service's environment settings (used to sign login
sessions). If you don't set one, the app generates a random one on startup, which means everyone gets logged
out whenever the server restarts — fine for testing, not ideal for production.

## Honest limitations
- Fee "reminders" generate a printable PDF letter — there's no email/SMS sending built in.
- No self-service "forgot password" flow yet; admin resets passwords manually under Manage Staff / Students.
- Backups are the whole database file — restoring one replaces *all* current data, so keep dated backups.
- No automated tests beyond manual smoke-testing of each route; treat this as a solid v1, not a finished
  audited product, if you plan to store sensitive data at scale.
