import logging
import re
from decimal import Decimal

import requests
from django.conf import settings as django_settings
from django.contrib.postgres.search import TrigramSimilarity
from django.db import connection
from django.db.models import Case, IntegerField, Q, Value, When
from google import genai
from google.genai import types

from api.models import BlogPost, CashbackTier, DeliveryCharge, Product, SiteSetting
from api.services.sms_service import _normalize_bd_phone

logger = logging.getLogger(__name__)

_MAX_TOOL_LOOPS = 5
# An ordering conversation naturally runs long — search, add a product,
# propose_order, add another, propose_order again, then name/phone/address —
# easily past a dozen turns before confirmation. 10 was cutting off the turns
# where earlier products were unambiguously identified, leaving the model
# with only a vague later reference (e.g. "the necklace") to go on.
_MAX_HISTORY_TURNS = 30

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
information to answer, answer immediately instead of calling more tools "to be thorough".
6. When search_products finds results, the app shows them automatically as picture cards BELOW \
your reply — never say "above" or "উপরে" when referring to them, always "below" / "নিচে", and \
don't re-describe every result's price/details in text since the cards already show that.
7. When search_blog_posts finds results, ALWAYS write each title as a markdown link using the \
url field it returned, e.g. "[পূজার নিয়মাবলী](https://example.com/blog/slug)" — never just write \
the title as plain text, the customer has no way to open the article otherwise."""

_ORDERING_INSTRUCTION = """

You can also place real Cash-on-Delivery orders for the customer using propose_order and \
create_order — the customer can order MULTIPLE different products together in one order, \
exactly like a normal cart checkout, not just one at a time. Follow this exact sequence, never \
skip a step:
1. To identify a product the customer wants to order, prefer calling add_order_item (or \
propose_order for the first item) DIRECTLY rather than a separate search_products call first — \
those tools already do exact matching and correctly surface ambiguity as clickable options for \
the customer. A plain search_products call can return several color/size variants at once (e.g. \
all colors of the same dress) with no such handling; if you then describe only ONE of those \
variants in your reply while the image carousel still shows every variant returned, that's a \
confusing mismatch — the customer sees 4 products pictured but you're only talking about 1. If \
you do use search_products to check something first, and it returns more than one matching \
variant, do NOT then commit to just one in your text — either ask the customer which variant \
they mean, or immediately call add_order_item/propose_order with every distinguishing detail \
they already gave (color, size) so the match narrows to exactly the one you're describing. Never \
propose or create an order on a guessed or ambiguous product name. If the customer wants several \
different products, resolve every one of them first.
2. Collect the customer's full name, phone number, delivery address, and district (the district \
is required — it determines the delivery charge automatically, inside vs. outside Dhaka).
3. Call propose_order(items, district) to build a preview — it's fine to call it before you have \
the customer's name/phone/address (e.g. just to show prices first), but before asking for final \
confirmation you must call it again including customer_name, phone, and address, so the preview \
shown to the customer also includes the full delivery summary, not just the product list. The app \
shows this preview to the customer automatically — product images, prices, delivery charge, grand \
total, and delivery info — with its own Confirm button, so do NOT repeat that price breakdown \
yourself in your reply. Just briefly acknowledge it and ask them to confirm, e.g. "Here's your \
order summary below — shall I place it?"
4. Only call create_order after the customer replies with a clear yes/confirmation \
("হ্যাঁ", "confirm", "order koro", etc.) in a later message, OR after clicking the Confirm button \
(which arrives as a message from them like any other). Never call create_order in the same turn \
you called propose_order, and never call it speculatively.
5. If the customer wants to add or remove a product before confirming, use add_order_item or \
remove_order_item — NEVER call propose_order again with a hand-typed full item list to do this, \
even though propose_order's parameters would technically allow it. Re-typing the full list \
requires you to correctly remember and re-list every item already in the order from earlier in \
the conversation, and you have been observed getting this wrong (dropping an item that was \
already added). add_order_item/remove_order_item only need the ONE item that's changing — \
everything else in the order carries forward automatically and correctly.
6. If propose_order, add_order_item, remove_order_item, or create_order returns an error (e.g. \
out of stock, product not found, or a \
name matching several products), explain it plainly using ONLY the information already in that \
error message and ask what they'd like to do instead. Never call search_products again to build a \
fresh suggestion list for this — that shows the customer a second, different set of products than \
the one you're describing in your reply, which is confusing. When the error lists several matching \
products, the app has already shown the customer those exact products as clickable buttons — do \
NOT re-list their names yourself, just briefly suggest they look at them (e.g. "মনে হচ্ছে আপনি এদের \
মধ্যে একটির কথা বলছেন — নিচে থেকে বেছে নিন।" / "Looks like you might mean one of these — pick \
yours below."). Clicking sends you their exact name as a normal message, so just continue the \
sequence from step 1 once you get it.
7. If the customer asks to see their order/cart/summary again — e.g. after asking about \
something unrelated in between — call show_order_summary instead of describing the items \
yourself in text. The app only redraws the order card on turns where an order tool actually \
runs, so without this the card just wouldn't reappear even though you're talking about it."""


def _word_set(text: str) -> set:
    # Split on punctuation too (not just whitespace), so a word glued to a
    # "|" separator or a Bengali দাঁড়ি ("।") still tokenizes correctly.
    return set(re.split(r'[\s|।,.:;!?()\-]+', text.lower()))


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
    match_score = Value(0, output_field=IntegerField())
    for word in name_words:
        word_q = (
            Q(name_bn__icontains=word) | Q(name_en__icontains=word)
            | Q(description_bn__icontains=word) | Q(description_en__icontains=word)
        )
        word_filter |= word_q
        match_score = match_score + Case(When(word_q, then=Value(1)), default=Value(0), output_field=IntegerField())

    # Ordered by a DB-side match count before slicing — without this, a
    # near-universal word like "গোপালের" (this shop's catalog is themed
    # entirely around "Gopal" products) can match hundreds of rows, and an
    # unordered LIMIT 50 could silently cut the actual best match before the
    # more precise whole-word scoring below ever gets to see it. This
    # ordering is still substring-based (not whole-word) so it's approximate,
    # but it only decides what makes it into the top-50 candidate window —
    # the whole-word relevance() scoring below remains the authority on the
    # final result.
    candidates = list(
        Product.objects.filter(is_active=True)
        .filter(word_filter)
        .annotate(match_score=match_score)
        .select_related('category').prefetch_related('images')
        .distinct().order_by('-match_score')[:50]
    )

    # Exact/substring matching found nothing — try typo-tolerant matching
    # before giving up (e.g. "golaper dress" when the real product is
    # "gopaler dress"). Postgres-only (pg_trgm, enabled via migration); on
    # MySQL this just no-ops and the search stays exact-match only.
    if not candidates and connection.vendor == 'postgresql':
        fuzzy = list(
            Product.objects.filter(is_active=True)
            .annotate(similarity=TrigramSimilarity('name_bn', query) + TrigramSimilarity('name_en', query))
            .filter(similarity__gt=0.15)
            .select_related('category').prefetch_related('images')
            .order_by('-similarity')[:20]
        )
        if not fuzzy:
            return []
        # A real typo has one clear winner well above the rest (e.g.
        # "golaper" vs "gopaler" dress). A product that simply doesn't exist
        # (e.g. searching "crown" when nothing crown-shaped is in the
        # catalog) instead produces a flat spread of weak, unrelated
        # matches — without this, that spread was flooding results with
        # random unrelated products instead of correctly finding nothing.
        best = fuzzy[0].similarity
        candidates = [p for p in fuzzy if p.similarity >= best - 0.03][:limit]
        logger.info(f'Support chat product search: exact match failed, fuzzy match used for {query!r}')
        return candidates

    # Inverse-document-frequency weighting: a query word that appears on
    # only a handful of products catalog-wide (e.g. "গণেশ" — a specific
    # deity name) is decisive; a word that appears on a large fraction of
    # the catalog (a generic connector like "ঠাকুর"/deity, "গোপালের"/this
    # shop's whole product line, or "বিগ্রহ"/idol-sized — glued onto dozens
    # of unrelated small accessories as a sizing descriptor) should barely
    # move the score. Measured against the WHOLE active catalog, not just
    # this query's candidate set — frequency within an already-filtered
    # candidate set is unreliable, since a word can look "rare" there by
    # coincidence while still being generic catalog-wide, which can flip
    # the ranking the wrong way.
    total_active = Product.objects.filter(is_active=True).count()
    lw_words = list({w.lower() for w in all_words})
    word_freq = {
        w: Product.objects.filter(is_active=True).filter(
            Q(name_bn__icontains=w) | Q(name_en__icontains=w)
            | Q(description_bn__icontains=w) | Q(description_en__icontains=w)
        ).count()
        for w in lw_words
    }

    def relevance(p):
        # Whole-word matching, not substring — "হার" (necklace) as a plain
        # substring check also matches inside unrelated words like "উপহার"
        # (gift, common boilerplate in nearly every product's description),
        # which was tying totally unrelated products (lamps, sweets, a
        # hairpiece) with the actual necklace and flooding the results.
        prod_name_words = _word_set(f'{p.name_bn} {p.name_en}')
        prod_desc_words = _word_set(f'{p.description_bn} {p.description_en}')
        score = 0.0
        for w in lw_words:
            weight = total_active / max(1, word_freq[w])
            # Name matches still count for more than description matches —
            # a product whose own name is the match is stronger evidence
            # than a word merely mentioned in its description text.
            if w in prod_name_words:
                score += 3.0 * weight
            elif w in prod_desc_words:
                score += 1.0 * weight
        return score

    scored = [(relevance(p), p) for p in candidates]
    best_score = max(score for score, _ in scored)
    # Keep only the best-matching tier, not everything that shares even one
    # generic word — e.g. searching "beguni khati kotton dress" (purple pure
    # cotton dress) was returning EVERY color variant of the same base dress,
    # because they all match "khati"/"kotton"/"dress"; only the purple one
    # also matches "beguni", so only it (and anything else tied with it)
    # should surface, not every same-word sibling product. Float scores now
    # (from the IDF weighting), so compare with a small tolerance.
    return [p for score, p in scored if score >= best_score - 1e-6][:limit]


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
        # A full, absolute URL (not a relative path) — the model puts this
        # directly into a markdown link in its reply, and a relative path
        # like "/blog/slug" would resolve against whatever page the chat
        # widget happens to be embedded on, not necessarily the storefront.
        'url': f'{django_settings.FRONTEND_URL}/blog/{p.slug}' if p.slug else None,
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


_DHAKA_DISTRICTS = {'dhaka', 'ঢাকা'}


def _serialize_candidate(p) -> dict:
    product_images = list(p.images.all())
    first_image = product_images[0] if product_images else None
    return {
        'product_id': str(p.id),
        'name_bn': p.name_bn,
        'name_en': p.name_en,
        'price': str(p.effective_price),
        'image_url': f'{django_settings.BACKEND_URL}{first_image.image.url}' if first_image else None,
    }


def _resolve_order_items(items: list):
    """Resolves each {'product_query', 'quantity'} entry to a real (Product,
    qty) pair, or returns an error. Shared by propose_order (preview only, no
    DB writes) and create_order (the real thing) so both agree on exactly
    which product an ambiguous or fuzzy query resolves to.

    Returns (resolved, error_message, candidates) — candidates is a list of
    brief product dicts (product_id, name, price, image) the frontend can
    render as clickable options, populated only when error_message is an
    ambiguous-match error; None otherwise."""
    if not items:
        return None, 'At least one product is required to place an order.', None
    resolved = []
    for entry in items:
        product_query = str(entry.get('product_query', '')).strip()
        if not product_query:
            return None, 'Each item needs a product name.', None
        matches = _find_products(product_query, limit=5)
        if not matches:
            return None, f'No product found matching "{product_query}". Try search_products again with a different name.', None
        if len(matches) > 1:
            names = [p.name_en or p.name_bn for p in matches]
            return (
                None,
                f'"{product_query}" matches multiple products: {names}. The customer has already '
                f'been shown these as clickable options in the app — just briefly ask them to pick '
                f'one, do not re-list the product names yourself.',
                [_serialize_candidate(p) for p in matches],
            )
        try:
            qty = max(1, int(entry.get('quantity') or 1))
        except (TypeError, ValueError):
            qty = 1
        resolved.append((matches[0], qty))
    return resolved, None, None


def _resolve_items_by_id(items: list):
    """Resolves items by the exact product_id a prior propose_order call
    already pinned down — no text search, so no ambiguity is possible. Used
    by create_order in preference to re-guessing from the model's own
    product_query text, which can drift from what was actually shown/agreed
    to once the customer has answered a few more messages (e.g. the model
    later refers to a product only by a generic word like "necklace", which
    can match several real products even though the customer's exact pick
    was already resolved earlier in the same order).

    Returns (resolved, error_message, candidates) — candidates is always
    None here (id-based lookup can't be ambiguous), kept only so callers can
    unpack both resolvers the same way."""
    resolved = []
    for entry in items:
        try:
            product = Product.objects.select_related('category').prefetch_related('images').get(
                id=entry.get('product_id'), is_active=True,
            )
        except (Product.DoesNotExist, ValueError, TypeError):
            return None, 'One of the previously selected products is no longer available. Please search for it again.', None
        try:
            qty = max(1, int(entry.get('quantity') or 1))
        except (TypeError, ValueError):
            qty = 1
        resolved.append((product, qty))
    return resolved, None, None


def _build_order_preview(
    resolved: list, district: str | None,
    customer_name: str | None = None, phone: str | None = None, address: str | None = None,
) -> dict:
    """Turns a resolved (Product, qty) list into the preview dict the frontend
    renders — product images, prices, delivery charge, grand total, and the
    delivery summary once known. Shared by propose_order and the incremental
    add/remove-item tools below, so all three build the identical shape."""
    preview_items = []
    subtotal = Decimal('0')
    for product, qty in resolved:
        effective = product.effective_price
        line_total = effective * qty
        subtotal += line_total
        product_images = list(product.images.all())
        first_image = product_images[0] if product_images else None
        preview_items.append({
            'product_id': str(product.id),
            'name_bn': product.name_bn,
            'name_en': product.name_en,
            'quantity': qty,
            'unit_price': str(effective),
            'line_total': str(line_total),
            'in_stock': product.stock_on_hand >= qty,
            'image_url': f'{django_settings.BACKEND_URL}{first_image.image.url}' if first_image else None,
        })

    dc = DeliveryCharge.get()
    delivery = dc.inside_dhaka if (district or '').strip().lower() in _DHAKA_DISTRICTS else dc.outside_dhaka
    grand_total = subtotal + delivery

    return {
        'items': preview_items,
        'subtotal': str(subtotal),
        'delivery_charge': str(delivery),
        'grand_total': str(grand_total),
        'payment_method': 'COD (Cash on Delivery)',
        'customer_name': (customer_name or '').strip() or None,
        'phone': (phone or '').strip() or None,
        'address': (address or '').strip() or None,
        'district': (district or '').strip() or None,
    }


def _propose_order(
    items: list, district: str,
    customer_name: str | None = None, phone: str | None = None, address: str | None = None,
) -> dict:
    """Resolves items and computes delivery charge/total WITHOUT placing the
    order — no DB writes, no stock deduction. Lets the frontend show a real
    preview (product images, prices, delivery charge, grand total, and the
    delivery name/phone/address once known) plus a Confirm button before the
    customer commits to anything.

    customer_name/phone/address are optional since an early preview (before
    those are collected) is still useful for showing prices — but once known,
    echoing them back here is what lets the preview show a full delivery
    summary, not just the product list."""
    resolved, error, candidates = _resolve_order_items(items)
    if error:
        return {'error': error, 'candidates': candidates} if candidates else {'error': error}
    return _build_order_preview(resolved, district, customer_name, phone, address)


def _add_order_item(product_query: str, quantity, current_order: dict | None = None) -> dict:
    """Adds ONE product to the order already being built, merging
    deterministically with whatever is already in current_order (the last
    preview shown to the customer, echoed back by the frontend) — the model
    never has to re-list every item already in the cart from memory, which
    it has been observed to silently drop items doing when composing a full
    propose_order call by hand."""
    if not current_order or not current_order.get('items'):
        return {'error': 'No order in progress yet — call propose_order first (with the district) to start one.'}

    matches = _find_products(product_query, limit=5)
    if not matches:
        return {'error': f'No product found matching "{product_query}". Try search_products again with a different name.'}
    if len(matches) > 1:
        return {
            'error': (
                f'"{product_query}" matches multiple products. The customer has already been '
                f'shown these as clickable options — just briefly ask them to pick one.'
            ),
            'candidates': [_serialize_candidate(p) for p in matches],
        }
    try:
        qty = max(1, int(quantity or 1))
    except (TypeError, ValueError):
        qty = 1

    id_items = [{'product_id': it['product_id'], 'quantity': it['quantity']} for it in current_order['items']]
    new_id = str(matches[0].id)
    for it in id_items:
        if it['product_id'] == new_id:
            it['quantity'] += qty
            break
    else:
        id_items.append({'product_id': new_id, 'quantity': qty})

    resolved, error, _ = _resolve_items_by_id(id_items)
    if error:
        return {'error': error}
    return _build_order_preview(
        resolved, current_order.get('district'),
        current_order.get('customer_name'), current_order.get('phone'), current_order.get('address'),
    )


def _remove_order_item(product_query: str, current_order: dict | None = None) -> dict:
    """Removes ONE product from the order already being built, identified by
    name against what's currently IN the cart (not the whole catalog) — the
    rest of the order carries forward automatically, same reasoning as
    add_order_item."""
    if not current_order or not current_order.get('items'):
        return {'error': 'No order in progress to remove an item from.'}

    query_norm = product_query.strip().lower()
    # Exact match first (handles clicking a candidate button, which sends
    # back the item's exact stored name — should always resolve to exactly
    # one item, unambiguously).
    exact = [
        it for it in current_order['items']
        if query_norm in (it['name_bn'].strip().lower(), it['name_en'].strip().lower())
    ]
    if len(exact) == 1:
        matches_in_cart = exact
    else:
        # Fall back to whole-word scoring (same principle as _find_products'
        # relevance ranking) — NOT a plain "any shared word" check, which
        # falsely tied together every item that merely shared a generic
        # connector word like "ডিজাইনার" (designer) or "ও" (and), even when
        # the customer specified one item's full, distinct name.
        query_words = _word_set(product_query)
        scored = [
            (len(query_words & _word_set(f"{it['name_bn']} {it['name_en']}")), it)
            for it in current_order['items']
        ]
        best_score = max(score for score, _ in scored)
        matches_in_cart = [it for score, it in scored if score == best_score] if best_score > 0 else []
    if not matches_in_cart:
        return {'error': f'"{product_query}" is not in the current order.'}
    if len(matches_in_cart) > 1:
        return {'error': f'"{product_query}" matches more than one item currently in the order — ask the customer to name the exact one to remove.'}
    if len(current_order['items']) == 1:
        return {'error': "That's the only item in the order — if the customer no longer wants to order at all, just don't call create_order."}

    remove_id = matches_in_cart[0]['product_id']
    id_items = [
        {'product_id': it['product_id'], 'quantity': it['quantity']}
        for it in current_order['items'] if it['product_id'] != remove_id
    ]
    resolved, error, _ = _resolve_items_by_id(id_items)
    if error:
        return {'error': error}
    return _build_order_preview(
        resolved, current_order.get('district'),
        current_order.get('customer_name'), current_order.get('phone'), current_order.get('address'),
    )


def _show_order_summary(current_order: dict | None = None) -> dict:
    """Re-displays the order already being built, completely unchanged. The
    frontend only redraws the order card on turns where an order tool is
    actually called — so if the customer asks to see their order again after
    talking about something else in between, without this the model has
    nothing to call and the card just wouldn't reappear."""
    if not current_order or not current_order.get('items'):
        return {'error': 'No order in progress yet.'}
    return current_order


def _create_order(
    items: list, customer_name: str, phone: str, address: str, district: str,
    pending_items: list | None = None,
) -> dict:
    """Places a real order — possibly multiple different products in one order,
    matching how a normal cart checkout works — by calling the existing public
    guest-checkout API (POST /api/guest/checkout/). Not a separate/duplicated
    code path: this gets exactly the same validation, stock deduction, and
    admin notifications as every other order in this app, and lands as
    PENDING for staff to review like any other order.

    items: [{'product_query': str, 'quantity': int}, ...] — the model's own
    guess at what's being ordered, used only as a fallback.

    pending_items: [{'product_id': str, 'quantity': int}, ...] — the exact
    products the customer already saw and agreed to in the last propose_order
    preview (echoed back by the frontend). Preferred over `items` whenever
    present: it's a resolved-by-ID snapshot the customer confirmed, so it
    can't suffer the ambiguity a fresh text search can (e.g. the model later
    referring to a product only as "the necklace", which may match several
    real products even though the customer's exact pick was already pinned
    down earlier in the same order)."""
    if pending_items:
        resolved, error, candidates = _resolve_items_by_id(pending_items)
    else:
        resolved, error, candidates = _resolve_order_items(items)
    if error:
        return {'error': error, 'candidates': candidates} if candidates else {'error': error}

    resolved_items = [{'product_id': str(product.id), 'quantity': qty} for product, qty in resolved]
    order_summary = [{'product': product.name_en or product.name_bn, 'quantity': qty} for product, qty in resolved]

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
    'propose_order': _propose_order,
    'add_order_item': _add_order_item,
    'remove_order_item': _remove_order_item,
    'show_order_summary': _show_order_summary,
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
_PROPOSE_ORDER_DECLARATION = types.FunctionDeclaration(
    name='propose_order',
    description=(
        "Builds an order PREVIEW — resolved products with images, unit prices, delivery "
        "charge, and grand total — WITHOUT placing anything (no stock deduction, nothing "
        "saved). The frontend shows this preview to the customer automatically with a "
        "Confirm button, so do NOT repeat the full price breakdown yourself in your reply — "
        "just briefly reference it (e.g. \"Here's your order summary below, shall I place it?\"). "
        "Always call this before create_order, once every product is identified and you have "
        "the customer's delivery district. Once you also know the customer's name, phone, and "
        "address, ALWAYS pass those too (call propose_order again with them if you called it "
        "earlier without them) — the preview then also shows the full delivery summary, not just "
        "the product list, so the customer can review everything together before confirming."
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
            'district': {'type': 'string', 'description': 'District, e.g. Dhaka, Chattogram — determines the delivery charge'},
            'customer_name': {'type': 'string', 'description': "Customer's full name, once known"},
            'phone': {'type': 'string', 'description': 'Bangladeshi mobile number, once known'},
            'address': {'type': 'string', 'description': 'Full delivery address, once known'},
        },
        'required': ['items', 'district'],
    },
)

_ADD_ORDER_ITEM_DECLARATION = types.FunctionDeclaration(
    name='add_order_item',
    description=(
        "Adds ONE product to the order that's already being built (i.e. propose_order has "
        "already been called at least once this conversation). ALWAYS prefer this over calling "
        "propose_order again with a full item list when the customer just wants to add one more "
        "thing — propose_order's full list requires you to correctly remember and re-type every "
        "item already in the order, which is error-prone over a long conversation; this only "
        "needs the ONE new item, everything else already in the order carries forward "
        "automatically and correctly. Returns the updated preview, same shape as propose_order."
    ),
    parameters={
        'type': 'object',
        'properties': {
            'product_query': {'type': 'string', 'description': 'Exact product name to add'},
            'quantity': {'type': 'integer', 'description': 'Number of units to add'},
        },
        'required': ['product_query', 'quantity'],
    },
)

_REMOVE_ORDER_ITEM_DECLARATION = types.FunctionDeclaration(
    name='remove_order_item',
    description=(
        "Removes ONE product from the order that's already being built. Identify it by the "
        "name it was ordered under — everything else already in the order carries forward "
        "automatically. Returns the updated preview, same shape as propose_order."
    ),
    parameters={
        'type': 'object',
        'properties': {
            'product_query': {'type': 'string', 'description': 'Name of the product to remove, as it appears in the order'},
        },
        'required': ['product_query'],
    },
)

_SHOW_ORDER_SUMMARY_DECLARATION = types.FunctionDeclaration(
    name='show_order_summary',
    description=(
        "Re-displays the order that's already being built, completely unchanged. Use this "
        "whenever the customer asks to see their order/cart/summary again — e.g. after asking "
        "about something else in between — instead of describing the items yourself in text. "
        "Takes no arguments."
    ),
    parameters={'type': 'object', 'properties': {}},
)

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


def answer(message: str, history: list[dict] | None = None, incoming_pending_order: dict | None = None) -> dict:
    """Runs the manual tool-calling loop against Gemini, scoped to product/pricing/
    discount/delivery/referral/cashback/blog data pulled live from the DB. Every
    function call the model makes is dispatched here, never executed by Gemini itself.

    incoming_pending_order: the last order preview the frontend showed the customer
    (echoed back on every request). Used as the trusted, already-resolved source of
    truth if the model calls create_order — never re-derived from the model's own
    text guess — and also carried forward as the returned pending_order when this
    turn doesn't touch the order at all, so the Confirm button doesn't vanish just
    because the customer asked an unrelated question in between.

    Returns {'reply': str, 'products': [...], 'pending_order': {...} | None}. Products
    shown to the customer are collected from the actual search_products tool results
    (not parsed out of the model's text), so the image/price/url the frontend renders
    is always real data, never something the model could hallucinate."""
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
        function_declarations.append(_PROPOSE_ORDER_DECLARATION)
        function_declarations.append(_ADD_ORDER_ITEM_DECLARATION)
        function_declarations.append(_REMOVE_ORDER_ITEM_DECLARATION)
        function_declarations.append(_SHOW_ORDER_SUMMARY_DECLARATION)
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
    pending_order = incoming_pending_order
    candidates: list[dict] = []
    order_updated = False

    for i in range(_MAX_TOOL_LOOPS):
        response = client.models.generate_content(model=model, contents=contents, config=config)
        calls = response.function_calls
        if not calls:
            if call_log:
                logger.info(f'Support chat resolved after {i} tool call(s): {call_log}')
            logger.info('Support chat final reply: %r', response.text or '')
            # Suppress the raw search_products carousel ONLY on a turn where
            # an order tool actually ran (order_updated) — the order card
            # itself is the single source of truth then, and a search made
            # purely to internally resolve one item (e.g. checking crown
            # color variants) would otherwise show products the reply text
            # isn't actually talking about. Gating on pending_order truthiness
            # instead of order_updated was too broad: it also hid genuine,
            # unrelated browsing (e.g. "show me some flutes") on any later
            # turn just because an order happened to still be in progress.
            products = [] if order_updated else shown_products
            return {
                'reply': response.text or '', 'products': products,
                'pending_order': pending_order, 'candidates': candidates,
                'order_updated': order_updated,
            }

        call_log.extend(f'{fc.name}({fc.args})' for fc in calls)
        contents.append(response.candidates[0].content)

        response_parts = []
        for fc in calls:
            handler = _TOOL_DISPATCH.get(fc.name)
            call_args = dict(fc.args or {})
            if fc.name in ('add_order_item', 'remove_order_item', 'show_order_summary'):
                # Use the LIVE pending_order (not the frozen snapshot from the
                # start of this request) so these still work correctly if the
                # model chains propose_order then add/remove_order_item
                # within the same turn, not just across separate messages.
                call_args['current_order'] = pending_order
            if fc.name == 'create_order' and incoming_pending_order:
                if incoming_pending_order.get('items'):
                    # Trust the exact products the customer already saw and
                    # agreed to (echoed back by the frontend) over the model's
                    # own text guess at what "the necklace" or similar refers
                    # to now.
                    call_args['pending_items'] = incoming_pending_order['items']
                # Same reasoning for the delivery details: place the order
                # against exactly what was shown in the last preview the
                # customer confirmed, not a value the model re-typed from
                # memory that could have drifted.
                for field in ('customer_name', 'phone', 'address', 'district'):
                    if incoming_pending_order.get(field):
                        call_args[field] = incoming_pending_order[field]
            try:
                result = handler(**call_args) if handler else {'error': f'Unknown tool: {fc.name}'}
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
            if fc.name in ('propose_order', 'add_order_item', 'remove_order_item', 'show_order_summary') \
                    and isinstance(result, dict) and 'error' not in result:
                pending_order = result
                candidates = []
                # Distinct from "pending_order carried forward unchanged" —
                # this specifically means an order tool actually ran THIS
                # turn, so the frontend should draw the card even if the
                # resulting order is identical to before (e.g. the customer
                # just asked to see it again via show_order_summary).
                order_updated = True
            if fc.name in ('propose_order', 'add_order_item', 'remove_order_item', 'create_order') \
                    and isinstance(result, dict) and result.get('candidates'):
                # An ambiguous product name — surfaced as clickable options
                # instead of making the customer retype the exact name.
                candidates = result['candidates']
            if fc.name == 'create_order' and isinstance(result, dict) and result.get('success'):
                # A real order was just placed — the earlier preview no longer
                # reflects reality (it hasn't been ordered yet), so drop it
                # rather than showing a stale "Confirm" button for an order
                # that's already been created.
                pending_order = None
                candidates = []
            response_parts.append(types.Part.from_function_response(name=fc.name, response=result))
        contents.append(types.Content(role='user', parts=response_parts))

    logger.warning(f'Support chat hit max tool-call loops without a final answer. Calls made: {call_log}')
    return {
        'reply': "Sorry, I'm having trouble answering that right now — please try again or contact support.",
        'products': shown_products,
        'pending_order': pending_order,
        'candidates': candidates,
        'order_updated': order_updated,
    }
