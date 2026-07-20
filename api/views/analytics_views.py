import logging

from django.conf import settings
from django.shortcuts import redirect
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.permissions import IsAdmin
from api.services.google_analytics_service import GoogleAnalyticsService, GoogleNotConnectedError
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)
_svc = GoogleAnalyticsService()


def _date_range(request):
    from datetime import date, timedelta
    to_str = request.query_params.get('to') or date.today().isoformat()
    from_str = request.query_params.get('from') or (date.today() - timedelta(days=29)).isoformat()
    return from_str, to_str


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def google_connect_url(request):
    return ApiResponse(message='Authorization URL generated', data={'auth_url': _svc.get_authorization_url()})


@api_view(['GET'])
@permission_classes([AllowAny])
def google_callback(request):
    code = request.query_params.get('code')
    error = request.query_params.get('error')
    if error or not code:
        return redirect(f'{settings.FRONTEND_URL}/admin/analytics?error=1')
    try:
        _svc.exchange_code_for_tokens(code, request.user if request.user.is_authenticated else None)
    except Exception:
        logger.exception('Google OAuth token exchange failed')
        return redirect(f'{settings.FRONTEND_URL}/admin/analytics?error=1')
    return redirect(f'{settings.FRONTEND_URL}/admin/analytics?connected=1')


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def google_status(request):
    return ApiResponse(message='Status retrieved', data=_svc.get_status())


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def google_properties(request):
    try:
        return ApiResponse(message='Properties retrieved', data={
            'ga4_properties': _svc.list_ga4_properties(),
            'gsc_sites': _svc.list_gsc_sites(),
        })
    except GoogleNotConnectedError as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)
    except Exception as e:
        logger.exception('Failed to list Google properties/sites')
        return ApiResponse(message='Failed to fetch Google properties', errors=str(e), status_code=502)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def google_select(request):
    data = request.data
    ga4_property_id = data.get('ga4_property_id', '')
    ga4_property_name = data.get('ga4_property_name', '')
    gsc_site_url = data.get('gsc_site_url', '')
    if not ga4_property_id or not gsc_site_url:
        return ApiResponse(message='ga4_property_id and gsc_site_url are required', status_code=400)
    _svc.select_property(ga4_property_id, ga4_property_name, gsc_site_url)
    return ApiResponse(message='Property selected', data=_svc.get_status())


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def google_disconnect(request):
    _svc.disconnect()
    return ApiResponse(message='Disconnected')


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def traffic_metrics(request):
    from_str, to_str = _date_range(request)
    try:
        return ApiResponse(message='Traffic metrics retrieved', data=_svc.get_traffic_metrics(from_str, to_str))
    except GoogleNotConnectedError as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)
    except Exception as e:
        logger.exception('Failed to fetch GA4 traffic metrics')
        return ApiResponse(message='Failed to fetch traffic metrics', errors=str(e), status_code=502)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def sales_metrics(request):
    from_str, to_str = _date_range(request)
    try:
        return ApiResponse(message='Sales metrics retrieved', data=_svc.get_sales_metrics(from_str, to_str))
    except GoogleNotConnectedError as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)
    except Exception as e:
        logger.exception('Failed to fetch GA4 sales metrics')
        return ApiResponse(message='Failed to fetch sales metrics', errors=str(e), status_code=502)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def seo_metrics(request):
    from_str, to_str = _date_range(request)
    try:
        return ApiResponse(message='SEO metrics retrieved', data=_svc.get_seo_metrics(from_str, to_str))
    except GoogleNotConnectedError as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)
    except Exception as e:
        logger.exception('Failed to fetch Search Console metrics')
        return ApiResponse(message='Failed to fetch SEO metrics', errors=str(e), status_code=502)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def pagespeed_seo(request):
    try:
        return ApiResponse(message='PageSpeed SEO score retrieved', data=_svc.get_pagespeed_seo())
    except GoogleNotConnectedError as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)
    except Exception as e:
        logger.exception('Failed to fetch PageSpeed Insights SEO score')
        return ApiResponse(message='Failed to fetch SEO score', errors=str(e), status_code=502)
