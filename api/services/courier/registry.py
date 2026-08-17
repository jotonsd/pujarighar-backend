from api.models import CourierProvider
from .base import BaseCourierService
from .pathao import PathaoCourierService
from .steadfast import SteadfastCourierService

PROVIDER_REGISTRY: dict[str, type[BaseCourierService]] = {
    'STEADFAST': SteadfastCourierService,
    'PATHAO': PathaoCourierService,
}


def get_courier_service(provider: CourierProvider) -> BaseCourierService:
    cls = PROVIDER_REGISTRY.get(provider.code)
    if not cls:
        raise Exception(f'No courier integration implemented for provider "{provider.code}"')
    return cls(provider)
