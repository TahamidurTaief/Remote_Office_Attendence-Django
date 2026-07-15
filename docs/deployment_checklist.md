# FieldTrack Deployment Checklist

Follow this checklist when deploying the FieldTrack application to a staging or production environment.

## 1. Environment Variables Configuration

Create a `.env` file in the project root directory or supply these variables directly to your container/hosting environment:

```env
# Set to False in staging and production!
DEBUG=False

# Django Secret Key (Generate a secure, random string)
SECRET_KEY=production-secret-key-change-me

# Redirection settings
SECURE_SSL_REDIRECT=True

# Database connection URL (e.g. PostgreSQL or SQLite)
# Example: postgres://db_user:db_password@db_host:5432/fieldtrack_db
DATABASE_URL=sqlite:///db.sqlite3

# Permitted hostname lists
ALLOWED_HOSTS=trackme.signtechlimited.com,localhost

# CSRF Trusted Origins (required for form submissions in Django 4.x+)
CSRF_TRUSTED_ORIGINS=https://trackme.signtechlimited.com

# Android TWA SHA-256 fingerprint for Digital Asset Links verification
# Example: 1A:2B:3C:... (colon-separated hash of your signing key)
TWA_SHA256_FINGERPRINT=REPLACE_WITH_REAL_SHA256_FINGERPRINT
```

---

## 2. local Build & Asset Steps

### Tailwind CSS Compilation
FieldTrack uses JIT-compiled Tailwind CSS. You must build the minified production CSS bundle before deploying:
1. Ensure Node.js is installed.
2. Initialize Node dependencies if not already done:
   ```bash
   python manage.py tailwind install
   ```
3. Run the production build command:
   ```bash
   python manage.py tailwind build
   ```

---

## 3. Database & Static Files

### Run Database Migrations
Always run database migrations to keep the database schema in sync:
```bash
python manage.py migrate
```

### Collect Static Files
Gather all static files (CSS, JS, images, fonts) into the root static assets directory:
```bash
python manage.py collectstatic --no-input
```

---

## 4. Audio & UI Assets Notes

### Sound-Effects Engine
- **No Physical Asset Files**: The application uses a synthesized Web Audio API sound effects engine directly in the browser. There are no `.mp3` or `.wav` files to host or manage.
- **Mute Toggle Persistence**: The user's audio preferences are saved in the client's browser `localStorage` (key: `sound_muted`). A mute toggle switch is available in both the admin and staff navigation layouts.
