import logging
from decimal import Decimal

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

_SYSTEM_INSTRUCTION = """You are Brahman AI, PujariGhar's product support assistant, embedded in \
a Bangladeshi religious/puja goods e-commerce storefront. If asked your name, you are "Brahman AI" \
("ব্রাহ্মণ এআই" in Bangla). You ONLY help customers with:
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


def _search_products(query: str) -> dict:
    products = (
        Product.objects.filter(is_active=True)
        .filter(
            Q(name_bn__icontains=query) | Q(name_en__icontains=query)
            | Q(description_bn__icontains=query) | Q(description_en__icontains=query)
        )
        .select_related('category')[:8]
    )
    results = []
    for p in products:
        original = p.original_price
        effective = p.effective_price
        discount_percent = None
        if original > 0 and effective < original:
            discount_percent = str((Decimal('100') * (1 - effective / original)).quantize(Decimal('0.1')))
        results.append({
            'name_bn': p.name_bn,
            'name_en': p.name_en,
            'price': str(effective),
            'original_price': str(original) if discount_percent else None,
            'discount_percent': discount_percent,
            'in_stock': p.stock_on_hand > 0,
            'url': f'/products/{p.slug}' if p.slug else None,
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


_TOOL_DISPATCH = {
    'search_products': _search_products,
    'get_delivery_charges': _get_delivery_charges,
    'get_referral_and_first_order_info': _get_referral_and_first_order_info,
    'get_cashback_tiers': _get_cashback_tiers,
    'search_blog_posts': _search_blog_posts,
    'get_contact_info': _get_contact_info,
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


def is_configured() -> bool:
    return bool(SiteSetting.get().gemini_api_key)


def answer(message: str, history: list[dict] | None = None) -> str:
    """Runs the manual tool-calling loop against Gemini, scoped to product/pricing/
    discount/delivery/referral/cashback/blog data pulled live from the DB. Every
    function call the model makes is dispatched here, never executed by Gemini itself."""
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

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_INSTRUCTION,
        tools=[types.Tool(function_declarations=_FUNCTION_DECLARATIONS)],
    )

    call_log: list[str] = []
    for i in range(_MAX_TOOL_LOOPS):
        response = client.models.generate_content(model=model, contents=contents, config=config)
        calls = response.function_calls
        if not calls:
            if call_log:
                logger.info(f'Support chat resolved after {i} tool call(s): {call_log}')
            return response.text or ''

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
            response_parts.append(types.Part.from_function_response(name=fc.name, response=result))
        contents.append(types.Content(role='user', parts=response_parts))

    logger.warning(f'Support chat hit max tool-call loops without a final answer. Calls made: {call_log}')
    return "Sorry, I'm having trouble answering that right now — please try again or contact support."
