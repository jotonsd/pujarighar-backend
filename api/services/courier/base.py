from abc import ABC, abstractmethod

from api.models import CourierConsignment, SalesOrder


class BaseCourierService(ABC):
    """Interface every courier provider implementation must follow. Methods
    that aren't universal across providers (e.g. police-station lookup is
    Steadfast-specific) default to NotImplementedError rather than being
    left out of the interface, so callers can always attempt them and get a
    clear error instead of an AttributeError.
    """

    @abstractmethod
    def create_order(self, order: SalesOrder, weight=None) -> dict:
        """Send an order to the courier. `weight` (kg) is optional and, when
        given, is passed through to the provider for shipping-charge
        calculation. Returns the raw provider response."""
        ...

    @abstractmethod
    def check_status(self, consignment: CourierConsignment) -> dict:
        """Poll the provider for the current status of a consignment."""
        ...

    @abstractmethod
    def get_balance(self) -> dict:
        ...

    def create_return_request(self, consignment: CourierConsignment, reason: str) -> dict:
        raise NotImplementedError('This provider does not support return requests')

    def get_return_request(self, provider_request_id: str) -> dict:
        raise NotImplementedError('This provider does not support return requests')

    def list_return_requests(self) -> dict:
        raise NotImplementedError('This provider does not support return requests')

    def list_payments(self) -> dict:
        raise NotImplementedError('This provider does not support payment listing')

    def get_payment(self, payment_id: str) -> dict:
        raise NotImplementedError('This provider does not support payment listing')

    def list_police_stations(self) -> dict:
        raise NotImplementedError('This provider does not support police-station lookup')
