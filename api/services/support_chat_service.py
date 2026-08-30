import logging
from decimal import Decimal

import requests
from django.conf import settings as django_settings
from django.contrib.postgres.search import TrigramSimilarity
from django.db import connection
from django.db.models import Q
from google import genai
from google.genai import types

from api.models import BlogPost, CashbackTier, DeliveryCharge, Product, SiteSetting
from api.services.sms_service import _normalize_bd_phone

logger = logging.getLogger(__name__)

_MAX_TOOL_LOOPS = 5
_MAX_HISTORY_TURNS = 10

# Mirrors frontend/src/utils/contact.ts's FACEBOOK_PAGE_ID / DEFAULT_EMAIL —
# there's no backend field for these yet, so keep both in sync if either ever changes.
_FACEBOOK_PAGE_ID = 'pujarighar'
_DEFAULT_EMAIL = 'pujarigharbd@gmail.com'

_SYSTEM_INSTRUCTION_BASE = """You are Brahman AI, PujariGhar's product support assistant, embedded in \
a Bangladeshi religious/puja goods e-commerce storefront. If asked your name, you are "Brahman AI" \
("ব্রাহ্মণ AI" in Bangla). You ONLY help customers with:
- Product information, pricing, and current discounts
- Delivery charges
- The referral bonus and first-order discount programs
- Cashback tiers
- Blog articles
- How to contact PujariGhar (WhatsApp, Messenger, Facebook page, email)

Rules:
1. ALWAYS call the provided tools to look up real data. Never state a price, discount \
percentage, delivery charge, bonus amount, or contact detail from memory or assumption — the \
catalog and settings change, so a guessed answer is worse than no answer.
2. If a question is about anything outside this scope (order status/tracking, account/login \
issues, admin/internal operations, or topics unrelated to PujariGhar's products or these \
programs), politely say you can only help with product, pricing, delivery, referral, cashback, \
blog, or contact questions, and suggest they reach out via the contact options for anything else.
3. Reply in the same language the customer wrote in — Bangla or English.
4. Keep answers concise and friendly, like a helpful shop assistant, not a wall of text.
5. Call each tool AT MOST ONCE per question unless the customer's message genuinely asks about \
several different things (e.g. both a product AND delivery charges). If a tool result is empty \
or unhelpful, say so plainly instead of calling the same or a similar tool again — do not retry \
searches with reworded queries hoping for a better result. As soon as you have enough \
information to answer, answer immediately instead of calling more tools "to be thorough"."""

_ORDERING_INSTRUCTION = """

You can also place real Cash-on-Delivery orders for the customer using create_order — the \
customer can order MULTIPLE different products together in one order, exactly like a normal \
cart checkout, not just one at a time. Follow this exact sequence, never skip a step:
1. Identify each exact product via search_products first — never call create_order on a guessed \
or ambiguous product name. If the customer wants several different products, resolve every one \
of them before proposing the order.
2. Collect the customer's full name, phone number, delivery address, and district (the district \
is required — it determines the delivery charge automatically, inside vs. outside Dhaka).
3. Before calling create_order, show the customer a clear summary in your reply: every product \
with its quantity and unit price, the delivery address, and that payment is Cash on Delivery — \
and explicitly ask them to confirm (e.g. "Shall I place this order?").
4. Only call create_order after the customer replies with a clear yes/confirmation \
("হ্যাঁ", "confirm", "order koro", etc.) in a later message. Never call it in the same turn you \
first proposed the order, and never call it speculatively.
5. If the customer wants to add another product to an order they haven't confirmed yet, update \
the summary and re-ask for confirmation — don't place a separate order per product.
6. If create_order returns an error (e.g. out of stock, product not found), explain it plainly \
and ask what they'd like to do instead — do not retry blindly."""


def _find_products(query: str, limit: int = 8) -> list:
    # Product names here are often long/compound (e.g. "পিতলের গণেশ ঠাকুর – ৫
    # ইঞ্চি | ১০০০ গ্রাম" — material + subject + size + weight all in one
    # field), so a literal substring match on the FULL query string misses
    # anything the customer phrases even slightly differently. Match word by
    # word instead (any word present = a candidate), then rank candidates by
    # how many of the query's words they actually contain, so the closest
    # match surfaces first instead of an arbitrary DB-order pick. Shared by
    # _search_products (browsing) and _create_order (must resolve to exactly
    # one product), so both see the same, better matching.
    # Keep single-digit tokens (e.g. "3" in "size 3 dress") for RANKING, but
    # never let a bare number qualify a product on its own — a garland whose
    # description says "3 feet long" would otherwise match a "size 3 dress"
    # query, since a lone digit is meaningless without the word next to it.
    all_words = [w for w in query.strip().split() if len(w) > 1 or w.isdigit()] or [query.strip()]
    name_words = [w for w in all_words if not w.isdigit()] or all_words

    word_filter = Q()
    for word in name_words:
        word_filter |= (
            Q(name_bn__icontains=word) | Q(name_en__icontains=word)
            | Q(description_bn__icontains=word) | Q(description_en__icontains=word)
        )

    candidates = list(
        Product.objects.filter(is_active=True)
        .filter(word_filter)
        .select_related('category').prefetch_related('images')
        .distinct()[:50]
    )

    # Exact/substring matching found nothing — try typo-tolerant matching
    # before giving up (e.g. "golaper dress" when the real product is
    # "gopaler dress"). Postgres-only (pg_trgm, enabled via migration); on
    # MySQL this just no-ops and the search stays exact-match only.
    if not candidates and connection.vendor == 'postgresql':
        candidates = list(
            Product.objects.filter(is_active=True)
            .annotate(similarity=TrigramSimilarity('name_bn', query) + TrigramSimilarity('name_en', query))
            .filter(similarity__gt=0.15)
            .select_related('category').prefetch_related('images')
            .order_by('-similarity')[:limit]
        )
        if candidates:
            logger.info(f'Support chat product search: exact match failed, fuzzy match used for {query!r}')
        return candidates

    def relevance(p):
        haystack = f'{p.name_bn} {p.name_en} {p.description_bn} {p.description_en}'.lower()
        return sum(1 for w in all_words if w.lower() in haystack)

    scored = [(relevance(p), p) for p in candidates]
    best_score = max(score for score, _ in scored)
    # Keep only the best-matching tier, not everything that shares even one
    # generic word — e.g. searching "beguni khati kotton dress" (purple pure
    # cotton dress) was returning EVERY color variant of the same base dress,
    # because they all match "khati"/"kotton"/"dress"; only the purple one
    # also matches "beguni", so only it (and anything else tied with it)
    # should surface, not every same-word sibling product.
    return [p for score, p in scored if score == best_score][:limit]


def _search_products(query: str) -> dict:
    products = _find_products(query)
    results = []
    for p in products:
        original = p.original_price
        effective = p.effective_price
        discount_percent = None
        if original > 0 and effective < original:
            discount_percent = str((Decimal('100') * (1 - effective / original)).quantize(Decimal('0.1')))
        # list(...all())[0] (not .first()) reuses the prefetch_related cache —
        # .first() re-queries the DB regardless of prefetching.
        product_images = list(p.images.all())
        first_image = product_images[0] if product_images else None
        results.append({
            'name_bn': p.name_bn,
            'name_en': p.name_en,
            'price': str(effective),
            'original_price': str(original) if discount_percent else None,
            'discount_percent': discount_percent,
            'in_stock': p.stock_on_hand > 0,
            'url': f'/products/{p.slug}' if p.slug else None,
            'image_url': f'{django_settings.BACKEND_URL}{first_image.image.url}' if first_image else None,
        })
    return {'products': results, 'count': len(results)}


def _get_delivery_charges() -> dict:
    dc = DeliveryCharge.get()
    return {
        'inside_dhaka': str(dc.inside_dhaka),
        'outside_dhaka': str(dc.outside_dhaka),
        'note': 'Heavier packages may cost more — the exact surcharge is set at delivery time.',
    }


def _get_referral_and_first_order_info() -> dict:
    s = SiteSetting.get()
    return {
        'referral_bonus_amount': str(s.referral_bonus_amount),
        'referral_bonus_description': "The referrer earns this amount as cashback once the "
            "customer they referred completes their first order.",
        'first_order_discount_percent': str(s.first_order_discount_percent),
        'first_order_discount_description': "A registered customer's very first self-checkout "
            "order automatically gets this percentage off, no code needed.",
    }


def _get_cashback_tiers() -> dict:
    tiers = CashbackTier.objects.filter(is_active=True).order_by('min_order_amount')
    return {
        'tiers': [{
            'min_order_amount': str(t.min_order_amount),
            'type': t.cashback_type,
            'value': str(t.cashback_value),
            'max_cashback': str(t.max_cashback) if t.max_cashback > 0 else None,
        } for t in tiers],
    }


def _search_blog_posts(query: str) -> dict:
    posts = (
        BlogPost.objects.filter(is_active=True, published_at__isnull=False)
        .filter(Q(title_bn__icontains=query) | Q(title_en__icontains=query))
        .order_by('-published_at')[:5]
    )
    return {'posts': [{
        'title_bn': p.title_bn,
        'title_en': p.title_en,
        'url': f'/blog/{p.slug}' if p.slug else None,
    } for p in posts]}


def _get_contact_info() -> dict:
    s = SiteSetting.get()
    wa_number = _normalize_bd_phone(s.contact_phone) if s.contact_phone else None
    return {
        'whatsapp_url': f'https://wa.me/{wa_number}' if wa_number else None,
        'messenger_url': f'https://m.me/{_FACEBOOK_PAGE_ID}',
        'facebook_page_url': f'https://www.facebook.com/{_FACEBOOK_PAGE_ID}',
        'email': s.contact_email or _DEFAULT_EMAIL,
        'phone': s.contact_phone or None,
    }


def _create_order(items: list, customer_name: str, phone: str, address: str, district: str) -> dict:
    """Places a real order — possibly multiple different products in one order,
    matching how a normal cart checkout works — by calling the existing public
    guest-checkout API (POST /api/guest/checkout/). Not a separate/duplicated
    code path: this gets exactly the same validation, stock deduction, and
    admin notifications as every other order in this app, and lands as
    PENDING for staff to review like any other order.

    items: [{'product_query': str, 'quantity': int}, ...]"""
    if not items:
        return {'error': 'At least one product is required to place an order.'}

    resolved_items = []
    order_summary = []
    for entry in items:
        product_query = str(entry.get('product_query', '')).strip()
        if not product_query:
            return {'error': 'Each item needs a product name.'}
        matches = _find_products(product_query, limit=5)
        if not matches:
            return {'error': f'No product found matching "{product_query}". Try search_products again with a different name.'}
        if len(matches) > 1:
            names = [p.name_en or p.name_bn for p in matches]
            return {'error': f'"{product_query}" matches multiple products: {names}. Ask the customer which exact one they want.'}

        product = matches[0]
        try:
            qty = max(1, int(entry.get('quantity') or 1))
        except (TypeError, ValueError):
            qty = 1
        resolved_items.append({'product_id': str(product.id), 'quantity': qty})
        order_summary.append({'product': product.name_en or product.name_bn, 'quantity': qty})

    if not (customer_name and str(customer_name).strip() and phone and str(phone).strip() and address and str(address).strip()):
        return {'error': 'Name, phone, and address are all required before placing an order.'}

    payload = {
        'items': resolved_items,
        'name_bn': customer_name,
        'name_en': customer_name,
        'phone': phone,
        'address_bn': address,
        'address_en': address,
        'district': district or '',
        'payment_method': 'COD',
    }
    try:
        resp = requests.post(f'{django_settings.BACKEND_URL}/api/cart/guest-checkout/', json=payload, timeout=15)
    except Exception as e:
        logger.error(f'Support chat order creation request failed (connection): {e}', exc_info=True)
        return {'error': 'Could not reach the order system right now. Please ask the customer to try again shortly.'}

    try:
        body = resp.json()
    except ValueError:
        logger.error(
            f'Support chat order creation got a non-JSON response | status={resp.status_code} | '
            f'url={resp.url} | body={resp.text[:1000]!r}'
        )
        return {'error': 'The order system returned an unexpected response. Please ask the customer to try again shortly.'}

    if resp.status_code >= 400:
        return {'error': body.get('errors') or body.get('message') or 'Order could not be placed.'}

    data = body.get('data', {})
    return {
        'success': True,
        'order_number': data.get('order_number'),
        'items': order_summary,
        'grand_total': data.get('grand_total'),
        'payment_method': 'COD (Cash on Delivery)',
        'status': data.get('status'),
    }


_TOOL_DISPATCH = {
    'search_products': _search_products,
    'get_delivery_charges': _get_delivery_charges,
    'get_referral_and_first_order_info': _get_referral_and_first_order_info,
    'get_cashback_tiers': _get_cashback_tiers,
    'search_blog_posts': _search_blog_posts,
    'get_contact_info': _get_contact_info,
    'create_order': _create_order,
}

_FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name='search_products',
        description="Search the live product catalog by name or description keywords. Returns "
            "current price, any active discount, and stock status.",
        parameters={
            'type': 'object',
            'properties': {'query': {'type': 'string', 'description': 'Product name or keyword to search for'}},
            'required': ['query'],
        },
    ),
    types.FunctionDeclaration(
        name='get_delivery_charges',
        description='Get the current delivery charges for inside and outside Dhaka.',
        parameters={'type': 'object', 'properties': {}},
    ),
    types.FunctionDeclaration(
        name='get_referral_and_first_order_info',
        description='Get the current referral bonus amount and first-order discount percentage.',
        parameters={'type': 'object', 'properties': {}},
    ),
    types.FunctionDeclaration(
        name='get_cashback_tiers',
        description='Get the current cashback tiers customers earn based on order amount.',
        parameters={'type': 'object', 'properties': {}},
    ),
    types.FunctionDeclaration(
        name='search_blog_posts',
        description='Search published blog articles by title keywords.',
        parameters={
            'type': 'object',
            'properties': {'query': {'type': 'string', 'description': 'Keyword to search blog titles for'}},
            'required': ['query'],
        },
    ),
    types.FunctionDeclaration(
        name='get_contact_info',
        description='Get PujariGhar\'s current contact options: WhatsApp link, Messenger link, '
            'Facebook page link, email, and phone number.',
        parameters={'type': 'object', 'properties': {}},
    ),
]

# Kept separate from _FUNCTION_DECLARATIONS and only appended when
# SiteSetting.ai_ordering_enabled is on — see answer() below. Placing a real
# order is meaningfully riskier than the read-only lookups above, so it's
# opt-in rather than available the moment a Gemini key is configured.
_CREATE_ORDER_DECLARATION = types.FunctionDeclaration(
    name='create_order',
    description=(
        "Places a real Cash-on-Delivery order — one or MORE different products in a single "
        "order, same as a normal cart checkout. Only call this after every product has been "
        "identified via search_products, the customer has provided their name, phone, address, "
        "and district, AND has explicitly confirmed they want to order in a follow-up message "
        "after seeing a summary. Never call this speculatively."
    ),
    parameters={
        'type': 'object',
        'properties': {
            'items': {
                'type': 'array',
                'description': 'One entry per distinct product the customer wants to order',
                'items': {
                    'type': 'object',
                    'properties': {
                        'product_query': {'type': 'string', 'description': 'Exact product name to order'},
                        'quantity': {'type': 'integer', 'description': 'Number of units to order'},
                    },
                    'required': ['product_query', 'quantity'],
                },
            },
            'customer_name': {'type': 'string', 'description': "Customer's full name"},
            'phone': {'type': 'string', 'description': 'Bangladeshi mobile number, e.g. 01XXXXXXXXX'},
            'address': {'type': 'string', 'description': 'Full delivery address'},
            'district': {'type': 'string', 'description': 'District, e.g. Dhaka, Chattogram — determines the delivery charge'},
        },
        'required': ['items', 'customer_name', 'phone', 'address', 'district'],
    },
)


def is_configured() -> bool:
    return bool(SiteSetting.get().gemini_api_key)


def answer(message: str, history: list[dict] | None = None) -> dict:
    """Runs the manual tool-calling loop against Gemini, scoped to product/pricing/
    discount/delivery/referral/cashback/blog data pulled live from the DB. Every
    function call the model makes is dispatched here, never executed by Gemini itself.

    Returns {'reply': str, 'products': [...]}. Products shown to the customer are
    collected from the actual search_products tool results (not parsed out of the
    model's text), so the image/price/url the frontend renders is always real data,
    never something the model could hallucinate."""
    s = SiteSetting.get()
    if not s.gemini_api_key:
        raise RuntimeError('Gemini API key is not configured')

    client = genai.Client(api_key=s.gemini_api_key)
    model = s.gemini_model or 'gemini-3.6-flash'

    contents: list[types.Content] = []
    for turn in (history or [])[-_MAX_HISTORY_TURNS:]:
        role = 'model' if turn.get('role') == 'model' else 'user'
        text = str(turn.get('text', ''))[:2000]
        if text:
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    contents.append(types.Content(role='user', parts=[types.Part(text=message[:2000])]))

    function_declarations = list(_FUNCTION_DECLARATIONS)
    system_instruction = _SYSTEM_INSTRUCTION_BASE
    if s.ai_ordering_enabled:
        function_declarations.append(_CREATE_ORDER_DECLARATION)
        system_instruction += _ORDERING_INSTRUCTION

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[types.Tool(function_declarations=function_declarations)],
    )

    # Full prompt visibility, on request — everything sent to Gemini for this
    # turn: the fixed system instruction, the trimmed conversation history,
    # and the new message.
    logger.info(
        'Support chat prompt | system_instruction=%r | history=%s | message=%r',
        system_instruction,
        [{'role': t.get('role'), 'text': str(t.get('text', ''))[:2000]} for t in (history or [])[-_MAX_HISTORY_TURNS:]],
        message[:2000],
    )

    call_log: list[str] = []
    shown_products: list[dict] = []
    seen_urls: set[str] = set()

    for i in range(_MAX_TOOL_LOOPS):
        response = client.models.generate_content(model=model, contents=contents, config=config)
        calls = response.function_calls
        if not calls:
            if call_log:
                logger.info(f'Support chat resolved after {i} tool call(s): {call_log}')
            logger.info('Support chat final reply: %r', response.text or '')
            return {'reply': response.text or '', 'products': shown_products}

        call_log.extend(f'{fc.name}({fc.args})' for fc in calls)
        contents.append(response.candidates[0].content)

        response_parts = []
        for fc in calls:
            handler = _TOOL_DISPATCH.get(fc.name)
            try:
                result = handler(**(fc.args or {})) if handler else {'error': f'Unknown tool: {fc.name}'}
            except Exception as e:
                logger.error(f'Support chat tool error ({fc.name}): {e}', exc_info=True)
                result = {'error': str(e)}
            logger.info('Support chat tool call | name=%s | args=%r | result=%r', fc.name, fc.args, result)
            if fc.name == 'search_products' and isinstance(result, dict):
                for prod in result.get('products', []):
                    image_url = prod.get('image_url')
                    if image_url and image_url not in seen_urls:
                        seen_urls.add(image_url)
                        shown_products.append(prod)
            response_parts.append(types.Part.from_function_response(name=fc.name, response=result))
        contents.append(types.Content(role='user', parts=response_parts))

    logger.warning(f'Support chat hit max tool-call loops without a final answer. Calls made: {call_log}')
    return {
        'reply': "Sorry, I'm having trouble answering that right now — please try again or contact support.",
        'products': shown_products,
    }
