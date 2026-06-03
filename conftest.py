import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(email='admin@test.com', phone='01900000001', password='Admin1234!', role='ADMIN')


@pytest.fixture
def customer_user(db):
    return User.objects.create_user(email='customer@test.com', phone='01900000002', password='Customer1!', role='CUSTOMER')


@pytest.fixture
def warehouse_user(db):
    return User.objects.create_user(email='warehouse@test.com', phone='01900000003', password='Warehouse1!', role='WAREHOUSE')


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def admin_client(api_client, admin_user):
    from rest_framework_simplejwt.tokens import RefreshToken
    token = RefreshToken.for_user(admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
    return api_client


@pytest.fixture
def customer_client(api_client, customer_user):
    from rest_framework_simplejwt.tokens import RefreshToken
    token = RefreshToken.for_user(customer_user)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
    return api_client
