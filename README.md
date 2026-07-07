# FieldTrack

FieldTrack is a Django-based attendance management system with geofencing.

## Tech Stack
- Django 5.x
- HTMX + Alpine.js
- Tailwind CSS

## Setup Instructions

1. Clone the repository and navigate to the project directory.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Configure the `.env` file.
4. Run migrations:
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   ```
5. Seed initial data:
   ```bash
   python manage.py seed_data
   ```

## Environment Variables (.env)
```env
DEBUG=True
SECRET_KEY=your-secret-key
DATABASE_URL=sqlite:///db.sqlite3
ALLOWED_HOSTS=*
```

## Running Locally
```bash
python manage.py runserver
```

## Default Login Credentials
- **Admin**: `admin@fieldtrack.com` / `admin123`
- **Staff**: `staff1@fieldtrack.com` / `staff123`
