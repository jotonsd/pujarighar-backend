import logging

from api.models import CourierProvider, DeliveryArea, DeliveryCity, DeliveryZone
from api.services.courier.pathao import PathaoCourierService

logger = logging.getLogger(__name__)


def _provider() -> CourierProvider:
    provider = CourierProvider.objects.filter(code='PATHAO', is_active=True).first()
    if not provider:
        raise Exception('No active Pathao courier provider is configured.')
    return provider


class DeliveryLocationService:
    """Plain reads of our own DeliveryCity/DeliveryZone/DeliveryArea tables
    (see models.py and the `seed_delivery_locations` management command,
    which does the one-time import) — the admin shipping form's dropdowns
    hit these, never Pathao's own city-list/zone-list/area-list directly.
    Pathao is just where this data was sourced from once; these rows have
    no runtime dependency on Pathao afterward."""

    def cities(self) -> list:
        return list(DeliveryCity.objects.all())

    def zones(self, city_pk: int) -> list:
        return list(DeliveryZone.objects.filter(city_id=city_pk))

    def areas(self, zone_pk: int) -> list:
        return list(DeliveryArea.objects.filter(zone_id=zone_pk))

    def import_from_pathao(self):
        """Pulls Pathao's full city → zone → area hierarchy and stores it
        in our own tables — a few hundred requests, meant to be run once
        (python manage.py seed_delivery_locations), not on any request path.
        Pathao's own numeric ids are only used transiently here (to fetch
        each level's children) and never persisted — rows are matched and
        de-duped by name instead."""
        svc = PathaoCourierService(_provider())

        for city_row in svc.list_cities():
            city, _ = DeliveryCity.objects.get_or_create(name=city_row['city_name'].strip())
            logger.info(f'Delivery city imported: {city.name}')
            for zone_row in svc.list_zones(city_row['city_id']):
                zone, _ = DeliveryZone.objects.get_or_create(city=city, name=zone_row['zone_name'].strip())
                for area_row in svc.list_areas(zone_row['zone_id']):
                    DeliveryArea.objects.get_or_create(zone=zone, name=area_row['area_name'].strip())
