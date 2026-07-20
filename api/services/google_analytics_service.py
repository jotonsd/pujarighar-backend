import logging
import os
import time
from datetime import datetime, timedelta

# Google always folds in previously-granted scopes (email/profile/openid from the
# existing login flow) alongside the ones we request here, since both flows share
# the same OAuth client. oauthlib treats "granted scopes != requested scopes" as
# fatal by default — relax that check, since a superset of granted scopes is fine.
os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Metric, OrderBy, RunReportRequest,
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from api.models import GoogleIntegration
from api.utils.crypto import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/analytics.readonly',
    'https://www.googleapis.com/auth/webmasters.readonly',
]

CACHE_TIMEOUT = 900  # 15 minutes — Google Analytics/Search Console APIs are rate-limited
PSI_CACHE_TIMEOUT = 86400  # 24 hours — a Lighthouse run is slow (~10-20s) and site SEO health doesn't change minute to minute


class GoogleNotConnectedError(Exception):
    pass


class GoogleAnalyticsService:

    # ─── OAuth ──────────────────────────────────────────────────────────────

    def _client_config(self) -> dict:
        return {
            'web': {
                'client_id': settings.GOOGLE_ANALYTICS_CLIENT_ID,
                'client_secret': settings.GOOGLE_ANALYTICS_CLIENT_SECRET,
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
                'redirect_uris': [settings.GOOGLE_ANALYTICS_REDIRECT_URI],
            }
        }

    def _flow(self) -> Flow:
        flow = Flow.from_client_config(self._client_config(), scopes=SCOPES)
        flow.redirect_uri = settings.GOOGLE_ANALYTICS_REDIRECT_URI
        return flow

    def get_authorization_url(self) -> str:
        auth_url, _state = self._flow().authorization_url(
            access_type='offline',
            prompt='consent',
            include_granted_scopes='true',
        )
        return auth_url

    def exchange_code_for_tokens(self, code: str, user) -> None:
        flow = self._flow()
        flow.fetch_token(code=code)
        creds = flow.credentials

        integration = GoogleIntegration.get()
        if creds.refresh_token:
            integration.refresh_token_encrypted = encrypt_token(creds.refresh_token)
        integration.access_token = creds.token or ''
        integration.token_expiry = creds.expiry
        integration.scopes = ' '.join(creds.scopes or SCOPES)
        integration.is_connected = True
        integration.connected_by = user
        integration.connected_at = timezone.now()
        integration.save()

    def get_credentials(self) -> Credentials:
        integration = GoogleIntegration.get()
        if not integration.is_connected or not integration.refresh_token_encrypted:
            raise GoogleNotConnectedError('Google account is not connected')

        refresh_token = decrypt_token(integration.refresh_token_encrypted)
        creds = Credentials(
            token=integration.access_token or None,
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=settings.GOOGLE_ANALYTICS_CLIENT_ID,
            client_secret=settings.GOOGLE_ANALYTICS_CLIENT_SECRET,
            scopes=SCOPES,
        )

        # Refresh if expired/near-expiry so the token stored below stays fresh.
        if not integration.token_expiry or integration.token_expiry <= timezone.now() + timedelta(minutes=2):
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            integration.access_token = creds.token
            integration.token_expiry = creds.expiry
            integration.save(update_fields=['access_token', 'token_expiry', 'updated_at'])

        return creds

    def disconnect(self) -> None:
        integration = GoogleIntegration.get()
        integration.is_connected = False
        integration.refresh_token_encrypted = ''
        integration.access_token = ''
        integration.token_expiry = None
        integration.ga4_property_id = ''
        integration.ga4_property_name = ''
        integration.gsc_site_url = ''
        integration.save()

    def get_status(self) -> dict:
        integration = GoogleIntegration.get()
        return {
            'is_connected': integration.is_connected,
            'ga4_property_id': integration.ga4_property_id,
            'ga4_property_name': integration.ga4_property_name,
            'gsc_site_url': integration.gsc_site_url,
            'connected_at': integration.connected_at,
            'has_selection': bool(integration.ga4_property_id and integration.gsc_site_url),
        }

    # ─── Discovery (property/site pickers) ─────────────────────────────────

    def list_ga4_properties(self) -> list[dict]:
        creds = self.get_credentials()
        service = build('analyticsadmin', 'v1beta', credentials=creds)
        result = []
        response = service.accountSummaries().list().execute()
        for account in response.get('accountSummaries', []):
            for prop in account.get('propertySummaries', []):
                result.append({
                    'property_id': prop['property'].split('/')[-1],
                    'display_name': prop.get('displayName', ''),
                    'account_name': account.get('displayName', ''),
                })
        return result

    def list_gsc_sites(self) -> list[dict]:
        creds = self.get_credentials()
        service = build('searchconsole', 'v1', credentials=creds)
        response = service.sites().list().execute()
        return [
            {'site_url': s['siteUrl'], 'permission_level': s.get('permissionLevel', '')}
            for s in response.get('siteEntry', [])
            if s.get('permissionLevel') in ('siteOwner', 'siteFullUser', 'siteRestrictedUser')
        ]

    def select_property(self, ga4_property_id: str, ga4_property_name: str, gsc_site_url: str) -> None:
        integration = GoogleIntegration.get()
        integration.ga4_property_id = ga4_property_id
        integration.ga4_property_name = ga4_property_name
        integration.gsc_site_url = gsc_site_url
        integration.save()

    # ─── Shared helpers ─────────────────────────────────────────────────────

    def _cached(self, key: str, fn, timeout: int = CACHE_TIMEOUT):
        value = cache.get(key)
        if value is not None:
            return value
        value = fn()
        cache.set(key, value, timeout)
        return value

    def _run_ga4_report(self, property_id: str, creds, **kwargs) -> RunReportRequest:
        client = BetaAnalyticsDataClient(credentials=creds)
        request = RunReportRequest(property=f'properties/{property_id}', **kwargs)
        return client.run_report(request)

    def _require_selection(self) -> GoogleIntegration:
        integration = GoogleIntegration.get()
        if not integration.is_connected:
            raise GoogleNotConnectedError('Google account is not connected')
        if not integration.ga4_property_id or not integration.gsc_site_url:
            raise GoogleNotConnectedError('No GA4 property / Search Console site selected yet')
        return integration

    # ─── Traffic ────────────────────────────────────────────────────────────

    def get_traffic_metrics(self, start_date: str, end_date: str) -> dict:
        integration = self._require_selection()

        def fetch():
            creds = self.get_credentials()
            date_range = [DateRange(start_date=start_date, end_date=end_date)]

            daily = self._run_ga4_report(
                integration.ga4_property_id, creds,
                dimensions=[Dimension(name='date')],
                metrics=[Metric(name='sessions'), Metric(name='totalUsers'), Metric(name='newUsers')],
                date_ranges=date_range,
                order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name='date'))],
            )
            daily_rows = [{
                'date': row.dimension_values[0].value,
                'sessions': int(row.metric_values[0].value),
                'total_users': int(row.metric_values[1].value),
                'new_users': int(row.metric_values[2].value),
            } for row in daily.rows]

            totals = self._run_ga4_report(
                integration.ga4_property_id, creds,
                metrics=[Metric(name='sessions'), Metric(name='totalUsers'), Metric(name='newUsers')],
                date_ranges=date_range,
            )
            if totals.rows:
                t = totals.rows[0].metric_values
                sessions_total, users_total, new_users_total = int(t[0].value), int(t[1].value), int(t[2].value)
            else:
                sessions_total = users_total = new_users_total = 0
            returning_users_total = max(users_total - new_users_total, 0)

            sources = self._run_ga4_report(
                integration.ga4_property_id, creds,
                dimensions=[Dimension(name='sessionDefaultChannelGroup')],
                metrics=[Metric(name='sessions')],
                date_ranges=date_range,
                order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name='sessions'), desc=True)],
                limit=10,
            )
            top_sources = [{
                'source': row.dimension_values[0].value,
                'sessions': int(row.metric_values[0].value),
            } for row in sources.rows]

            return {
                'sessions_total': sessions_total,
                'users_total': users_total,
                'new_users_total': new_users_total,
                'returning_users_total': returning_users_total,
                'daily': daily_rows,
                'top_traffic_sources': top_sources,
            }

        return self._cached(f'ga4:traffic:{integration.ga4_property_id}:{start_date}:{end_date}', fetch)

    # ─── Sales (GA4 ecommerce) ──────────────────────────────────────────────

    def get_sales_metrics(self, start_date: str, end_date: str) -> dict:
        integration = self._require_selection()

        def fetch():
            from google.analytics.data_v1beta.types import Filter, FilterExpression

            creds = self.get_credentials()
            date_range = [DateRange(start_date=start_date, end_date=end_date)]

            events = self._run_ga4_report(
                integration.ga4_property_id, creds,
                dimensions=[Dimension(name='eventName')],
                metrics=[Metric(name='eventCount')],
                date_ranges=date_range,
                dimension_filter=FilterExpression(filter=Filter(
                    field_name='eventName',
                    in_list_filter=Filter.InListFilter(values=['add_to_cart', 'begin_checkout', 'purchase']),
                )),
            )
            counts = {row.dimension_values[0].value: int(row.metric_values[0].value) for row in events.rows}
            add_to_cart_count = counts.get('add_to_cart', 0)
            checkout_starts = counts.get('begin_checkout', 0)
            purchases = counts.get('purchase', 0)

            revenue_report = self._run_ga4_report(
                integration.ga4_property_id, creds,
                metrics=[Metric(name='purchaseRevenue'), Metric(name='transactions'), Metric(name='sessions')],
                date_ranges=date_range,
            )
            if revenue_report.rows:
                r = revenue_report.rows[0].metric_values
                revenue = float(r[0].value)
                transactions = int(float(r[1].value))
                sessions = int(r[2].value)
            else:
                revenue = 0.0
                transactions = 0
                sessions = 0

            aov = revenue / transactions if transactions else 0.0
            conversion_rate = (transactions / sessions * 100) if sessions else 0.0
            cart_abandonment_rate = ((add_to_cart_count - purchases) / add_to_cart_count * 100) if add_to_cart_count else 0.0

            products = self._run_ga4_report(
                integration.ga4_property_id, creds,
                dimensions=[Dimension(name='itemName')],
                metrics=[Metric(name='itemsPurchased'), Metric(name='itemRevenue')],
                date_ranges=date_range,
                order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name='itemRevenue'), desc=True)],
                limit=10,
            )
            top_products = [{
                'name': row.dimension_values[0].value,
                'units_sold': int(float(row.metric_values[0].value)),
                'revenue': float(row.metric_values[1].value),
            } for row in products.rows]

            return {
                'add_to_cart_count': add_to_cart_count,
                'checkout_starts': checkout_starts,
                'purchases': purchases,
                'revenue': revenue,
                'conversion_rate': round(conversion_rate, 2),
                'average_order_value': round(aov, 2),
                'cart_abandonment_rate': round(cart_abandonment_rate, 2),
                'top_selling_products': top_products,
            }

        return self._cached(f'ga4:sales:{integration.ga4_property_id}:{start_date}:{end_date}', fetch)

    # ─── SEO (Search Console + CrUX) ────────────────────────────────────────

    def get_seo_metrics(self, start_date: str, end_date: str) -> dict:
        integration = self._require_selection()

        def fetch():
            creds = self.get_credentials()
            service = build('searchconsole', 'v1', credentials=creds)
            site_url = integration.gsc_site_url

            def query(dimensions, row_limit=1000):
                body = {'startDate': start_date, 'endDate': end_date, 'dimensions': dimensions, 'rowLimit': row_limit}
                return service.searchanalytics().query(siteUrl=site_url, body=body).execute().get('rows', [])

            daily_rows = query(['date'])
            daily = [{
                'date': row['keys'][0],
                'clicks': row['clicks'],
                'impressions': row['impressions'],
                'ctr': round(row['ctr'] * 100, 2),
                'position': round(row['position'], 1),
            } for row in daily_rows]

            clicks_total = sum(d['clicks'] for d in daily)
            impressions_total = sum(d['impressions'] for d in daily)
            ctr_total = round(clicks_total / impressions_total * 100, 2) if impressions_total else 0.0
            avg_position = round(sum(row['position'] for row in daily_rows) / len(daily_rows), 1) if daily_rows else 0.0

            top_queries = [{
                'query': row['keys'][0], 'clicks': row['clicks'], 'impressions': row['impressions'],
                'ctr': round(row['ctr'] * 100, 2), 'position': round(row['position'], 1),
            } for row in query(['query'], row_limit=10)]

            top_pages = [{
                'page': row['keys'][0], 'clicks': row['clicks'], 'impressions': row['impressions'],
                'ctr': round(row['ctr'] * 100, 2), 'position': round(row['position'], 1),
            } for row in query(['page'], row_limit=10)]

            indexed_pages = self._get_indexed_pages_estimate(service, site_url)
            core_web_vitals = self._get_core_web_vitals(site_url)

            return {
                'clicks_total': clicks_total,
                'impressions_total': impressions_total,
                'ctr_total': ctr_total,
                'avg_position': avg_position,
                'daily': daily,
                'top_queries': top_queries,
                'top_pages': top_pages,
                'indexed_pages_estimate': indexed_pages,
                'core_web_vitals': core_web_vitals,
            }

        return self._cached(f'gsc:seo:{integration.gsc_site_url}:{start_date}:{end_date}', fetch)

    def _get_indexed_pages_estimate(self, service, site_url: str) -> dict:
        """Best-effort indexed-page count via the Sitemaps API — Search Console has
        no public API for the exact 'Page indexing' total shown in its own UI."""
        try:
            sitemaps = service.sitemaps().list(siteUrl=site_url).execute().get('sitemap', [])
        except Exception as e:
            logger.warning(f'Sitemaps API call failed: {e}')
            return {'available': False, 'indexed': 0, 'submitted': 0}

        if not sitemaps:
            return {'available': False, 'indexed': 0, 'submitted': 0}

        indexed = submitted = 0
        for sm in sitemaps:
            for content in sm.get('contents', []):
                submitted += int(content.get('submitted', 0))
                indexed += int(content.get('indexed', 0))
        return {'available': True, 'indexed': indexed, 'submitted': submitted}

    def _normalize_origin(self, site_url: str) -> str:
        """GSC site URLs are either a real URL-prefix property or a 'sc-domain:example.com'
        domain property — normalize either to a plain https:// origin/URL for other APIs."""
        origin = site_url.rstrip('/')
        if origin.startswith('sc-domain:'):
            origin = f'https://{origin.split(":", 1)[1]}'
        return origin

    def _get_core_web_vitals(self, site_url: str) -> dict:
        """Field data from the public Chrome UX Report API — Search Console's old
        'Mobile Usability' report was fully retired by Google in Dec 2023."""
        api_key = settings.CRUX_API_KEY
        if not api_key:
            return {'available': False, 'reason': 'CRUX_API_KEY not configured'}

        origin = self._normalize_origin(site_url)

        try:
            resp = requests.post(
                f'https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={api_key}',
                json={'origin': origin, 'formFactor': 'PHONE'},
                timeout=10,
            )
        except requests.RequestException as e:
            logger.warning(f'CrUX API request failed: {e}')
            return {'available': False, 'reason': 'request_failed'}

        if resp.status_code == 404:
            return {'available': False, 'reason': 'insufficient_traffic_data'}
        if resp.status_code != 200:
            logger.warning(f'CrUX API returned {resp.status_code}: {resp.text[:200]}')
            return {'available': False, 'reason': 'api_error'}

        metrics = resp.json().get('record', {}).get('metrics', {})

        def bucket_pct(metric_key):
            hist = metrics.get(metric_key, {}).get('histogram', [])
            if not hist:
                return None
            return {
                'good': round(hist[0].get('density', 0) * 100, 1) if len(hist) > 0 else 0,
                'needs_improvement': round(hist[1].get('density', 0) * 100, 1) if len(hist) > 1 else 0,
                'poor': round(hist[2].get('density', 0) * 100, 1) if len(hist) > 2 else 0,
            }

        return {
            'available': True,
            'largest_contentful_paint': bucket_pct('largest_contentful_paint'),
            'interaction_to_next_paint': bucket_pct('interaction_to_next_paint'),
            'cumulative_layout_shift': bucket_pct('cumulative_layout_shift'),
        }

    # ─── PageSpeed Insights / Lighthouse (Performance, Accessibility, Best Practices, SEO) ──

    PSI_CATEGORIES = ['PERFORMANCE', 'ACCESSIBILITY', 'BEST_PRACTICES', 'SEO']
    PSI_LAB_METRICS = [
        'first-contentful-paint', 'largest-contentful-paint',
        'total-blocking-time', 'cumulative-layout-shift', 'speed-index',
    ]

    def get_pagespeed_seo(self, force: bool = False) -> dict:
        """Both Mobile and Desktop Lighthouse results (Performance/Accessibility/Best
        Practices/SEO scores + failing checks + key lab metrics), via the public
        PageSpeed Insights API. Unlike the sitemap-based indexed-page estimate, this is
        real, actionable data available for any site regardless of traffic volume.
        `force=True` bypasses the 24h cache and re-runs Lighthouse fresh (used by the
        manual Refresh action) — the fresh result then replaces the cached value, so
        subsequent normal loads pick it up too."""
        integration = self._require_selection()
        url = self._normalize_origin(integration.gsc_site_url) + '/'
        result = {}
        for strategy in ('MOBILE', 'DESKTOP'):
            key = f'psi:v2:{url}:{strategy}'
            if force:
                cache.delete(key)
            result[strategy.lower()] = self._cached(
                key, lambda s=strategy: self._fetch_pagespeed_seo(url, s), timeout=PSI_CACHE_TIMEOUT,
            )
        return result

    PSI_MAX_ATTEMPTS = 3
    PSI_RETRY_DELAY_SECONDS = 2

    def _request_psi(self, url: str, api_key: str, strategy: str):
        """PageSpeed Insights' Lighthouse runner intermittently returns transient
        5xx errors, especially on a cold/uncached URL — retry a couple of times
        before giving up, rather than surfacing a one-off blip as a hard failure."""
        last_error = None
        for attempt in range(1, self.PSI_MAX_ATTEMPTS + 1):
            try:
                resp = requests.get(
                    'https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed',
                    params=[('url', url), ('key', api_key), ('strategy', strategy)]
                           + [('category', c) for c in self.PSI_CATEGORIES],
                    timeout=45,
                )
            except requests.RequestException as e:
                last_error = ('request_failed', str(e))
                logger.warning(f'PageSpeed Insights request failed (attempt {attempt}/{self.PSI_MAX_ATTEMPTS}, {strategy}): {e}')
            else:
                if resp.status_code == 200:
                    return resp, None
                last_error = ('api_error', f'{resp.status_code}: {resp.text[:200]}')
                logger.warning(f'PageSpeed Insights returned {resp.status_code} (attempt {attempt}/{self.PSI_MAX_ATTEMPTS}, {strategy}): {resp.text[:200]}')

            if attempt < self.PSI_MAX_ATTEMPTS:
                time.sleep(self.PSI_RETRY_DELAY_SECONDS)

        return None, last_error[0]

    def _fetch_pagespeed_seo(self, url: str, strategy: str) -> dict:
        api_key = settings.CRUX_API_KEY
        if not api_key:
            return {'available': False, 'reason': 'CRUX_API_KEY not configured'}

        resp, error_reason = self._request_psi(url, api_key, strategy)
        if resp is None:
            return {'available': False, 'reason': error_reason}

        lighthouse = resp.json().get('lighthouseResult', {})
        categories = lighthouse.get('categories', {})
        audits = lighthouse.get('audits', {})

        scores = {}
        failing_issues = {}
        for key, cat_id in [('performance', 'performance'), ('accessibility', 'accessibility'),
                             ('best_practices', 'best-practices'), ('seo', 'seo')]:
            category = categories.get(cat_id, {})
            score = category.get('score')
            scores[key] = round(score * 100) if score is not None else None

            issues = []
            for ref in category.get('auditRefs', []):
                audit = audits.get(ref['id'], {})
                if audit.get('scoreDisplayMode') != 'binary' or audit.get('score') == 1:
                    continue
                issues.append({'title': audit.get('title', ref['id']), 'description': audit.get('description', '')})
            failing_issues[key] = issues[:10]

        if scores['seo'] is None:
            return {'available': False, 'reason': 'no_score_returned'}

        lab_metrics = {
            metric_id: audits[metric_id]['displayValue']
            for metric_id in self.PSI_LAB_METRICS
            if metric_id in audits and 'displayValue' in audits[metric_id]
        }

        return {
            'available': True,
            'strategy': strategy,
            'scores': scores,
            'failing_issues': failing_issues,
            'lab_metrics': lab_metrics,
        }
