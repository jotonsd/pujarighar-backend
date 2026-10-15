"""One-time import of Pathao's full City -> Zone -> Area hierarchy into our
own DeliveryCity/DeliveryZone/DeliveryArea tables, so the admin
order-shipping form's dropdowns read from our own database instead of
calling Pathao's city-list/zone-list/area-list live on every request.
These rows are ours from then on — nothing about them depends on Pathao
specifically still being the configured courier later.

Usage:
    python manage.py seed_delivery_locations
"""
from django.core.management.base import BaseCommand

from api.models import DeliveryArea, DeliveryCity, DeliveryZone
from api.services.courier.pathao_location import DeliveryLocationService


class Command(BaseCommand):
    help = "Imports Pathao's city/zone/area lists into our own delivery-location tables"

    def handle(self, *args, **options):
        DeliveryLocationService().import_from_pathao()
        self.stdout.write(self.style.SUCCESS(
            f'Done — {DeliveryCity.objects.count()} cities, '
            f'{DeliveryZone.objects.count()} zones, {DeliveryArea.objects.count()} areas stored.'
        ))
