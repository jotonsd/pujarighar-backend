# PujariGhar — Backend

REST API for the PujariGhar e-commerce platform. Built with **Django 5.1** and **Django REST Framework**.

---

## Tech Stack

| Tool | Version |
|------|---------|
| Python | 3.13 |
| Django | 5.1.9 |
| Django REST Framework | 3.15.2 |
| SimpleJWT | 5.3.1 |
| PostgreSQL | via psycopg2 |
| Pillow | 11.2.1 |

---

## Getting Started

### Prerequisites
- Python 3.11+
- PostgreSQL running locally

### Setup

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env          # edit with your DB credentials

# Run migrations
python manage.py migrate

# Seed initial data
python manage.py seed_users   # creates admin & customer accounts

# Start server
python manage.py runserver 8020
```

API available at **http://localhost:8020**

---

## Environment Variables

Create a `.env` file in the project root:

```env
SECRET_KEY=your-django-secret-key
DEBUG=True
DB_NAME=pujarighar
DB_USER=postgres
DB_PASSWORD=yourpassword
DB_HOST=localhost
DB_PORT=5432
ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=http://localhost:3000
```

---

## Project Structure

```
backend/
├── api/
│   ├── models.py               # All models (Product, Order, Banner, etc.)
│   ├── urls.py                 # All URL patterns
│   ├── permissions.py          # IsAdmin, IsWarehouse custom permissions
│   ├── views/                  # View functions per domain
│   │   ├── auth_views.py
│   │   ├── product_views.py
│   │   ├── order_views.py
│   │   ├── banner_views.py
│   │   ├── hero_slide_views.py
│   │   └── ...
│   ├── serializers/            # DRF serializers per domain
│   ├── services/               # Business logic layer
│   └── utils/
│       ├── response.py         # Unified ApiResponse helper
│       └── pagination.py
├── pujarighar/
│   └── settings.py
├── media/                      # Uploaded images (gitignored)
├── requirements.txt
└── manage.py
```

---

## API Overview

| Prefix | Description |
|--------|-------------|
| `/api/auth/` | Register, login, logout, token refresh |
| `/api/products/` | Product CRUD, images, package items |
| `/api/categories/` | Category management |
| `/api/orders/` | Sales orders, POS create, status flow |
| `/api/cart/` | Cart management |
| `/api/banners/` | Offer banners |
| `/api/hero-slides/` | Hero slider images |
| `/api/users/` | User management |
| `/api/accounting/` | Journal entries, ledger, reports |
| `/api/dashboard/` | Summary stats |

All responses use the unified format:
```json
{
  "status": "success",
  "message": "...",
  "data": {},
  "pagination": {}
}
```

---

## Roles

| Role | Access |
|------|--------|
| `ADMIN` | Full access |
| `WAREHOUSE` | Products, inventory, orders |
| `DELIVERY` | Assigned delivery orders |
| `CUSTOMER` | Own orders, cart, profile |

---

## Running Tests

```bash
pytest
```
