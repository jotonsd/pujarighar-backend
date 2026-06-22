"""
Management command: create_admin

Creates a real ADMIN user for production use (unlike seed_pujarighar's demo
accounts which use known, public passwords).

Usage:
    python manage.py create_admin
    python manage.py create_admin --email admin@example.com --phone 01700000000 --password "Secret123!"
"""

import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = 'Create an ADMIN user'

    def add_arguments(self, parser):
        parser.add_argument('--email', help='Admin email address')
        parser.add_argument('--phone', help='Admin phone number')
        parser.add_argument('--password', help='Admin password (prompted securely if omitted)')
        parser.add_argument('--name', default='', help='Full name (optional)')

    @transaction.atomic
    def handle(self, *args, **options):
        from api.models import User

        email = options['email'] or input('Email: ').strip()
        if not email:
            raise CommandError('Email is required')
        if User.objects.filter(email=email).exists():
            raise CommandError(f'A user with email "{email}" already exists')

        phone = options['phone'] or input('Phone: ').strip()
        if not phone:
            raise CommandError('Phone is required')
        if User.objects.filter(phone=phone).exists():
            raise CommandError(f'A user with phone "{phone}" already exists')

        password = options['password']
        if not password:
            password = getpass.getpass('Password: ')
            if password != getpass.getpass('Password (again): '):
                raise CommandError('Passwords did not match')

        try:
            validate_password(password)
        except DjangoValidationError as e:
            raise CommandError('\n'.join(e.messages))

        name = options['name']

        user = User.objects.create_user(
            email=email, phone=phone, password=password,
            role='ADMIN', is_staff=True, is_superuser=True,
        )
        if name:
            user.profile.full_name_bn = name
            user.profile.full_name_en = name
            user.profile.save()

        self.stdout.write(self.style.SUCCESS(f'✓ Admin user created: {email}'))
