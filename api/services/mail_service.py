import logging
import re
import threading
from decimal import Decimal
from email.utils import formataddr

from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import close_old_connections
from django.utils import timezone

from api.models import SiteSetting, User

logger = logging.getLogger(__name__)


def _get_connection():
    s = SiteSetting.get()
    if not s.email_host or not s.email_host_user:
        return None, None
    # Port 465 is implicit-SSL; STARTTLS (use_tls) only applies to 587/25.
    use_ssl = s.email_port == 465
    conn = get_connection(
        backend='django.core.mail.backends.smtp.EmailBackend',
        host=s.email_host,
        port=s.email_port,
        username=s.email_host_user,
        password=s.email_host_password,
        use_tls=s.email_use_tls and not use_ssl,
        use_ssl=use_ssl,
        fail_silently=False,
    )
    address  = s.email_default_from or s.email_host_user
    sender_name = f"{s.company_name_bn} | {s.company_name_en}" if s.company_name_bn or s.company_name_en else "PujariGhar"
    from_email = formataddr((sender_name, address))
    return conn, from_email


def _admin_emails():
    return list(
        User.objects.filter(role__code='ADMIN', is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )


def _html_to_text(html: str) -> str:
    text = re.sub(r'<br\s*/?>|</tr>|</p>|</h\d>', '\n', html)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


def _send_async(subject, html_body, recipients):
    def _send():
        try:
            conn, from_email = _get_connection()
            if not conn:
                logger.warning(f"Mail skipped (no SMTP configured): subject={subject!r} recipients={recipients}")
                return
            if not recipients:
                logger.warning(f"Mail skipped (no recipients): subject={subject!r}")
                return
            s = SiteSetting.get()
            reply_to = [s.email_default_from or s.email_host_user]
            msg = EmailMultiAlternatives(
                subject,
                _html_to_text(html_body),
                from_email,
                recipients,
                reply_to=reply_to,
                connection=conn,
            )
            msg.attach_alternative(html_body, 'text/html')
            msg.send()
            logger.info(f"Mail sent: subject={subject!r} recipients={recipients}")
        except Exception as e:
            logger.error(f"Mail send error: subject={subject!r} recipients={recipients} error={e}", exc_info=True)
    threading.Thread(target=_send, daemon=True).start()


def _customer_email(order) -> str | None:
    email = (
        (order.customer.email if order.customer else None)
        or order.guest_email
        or None
    )
    return email.strip() if email and email.strip() else None


def _customer_lang(order) -> str:
    if order.customer and order.customer.preferred_language:
        return order.customer.preferred_language
    return 'bn'


def _customer_display(order) -> str:
    """Admin-facing "who placed this order" line. Guests never have an
    email (it's not collected at guest/POS checkout), only a name + phone —
    showing just the email (or 'Guest' when it's blank) silently dropped
    that name/phone, which is the only identifying info admin has for them."""
    name  = order.shipping_name_bn or order.shipping_name_en or ''
    parts = [p for p in [name, order.shipping_phone] if p]
    label = ' — '.join(parts) if parts else 'Guest'
    email = _customer_email(order)
    return f'{label} ({email})' if email else label


def _fmt(value) -> str:
    try:
        return f'{Decimal(str(value)):,.2f}'
    except Exception:
        return '0.00'


def _order_summary_html(order, is_bn: bool):
    name = (lambda p: p.name_bn) if is_bn else (lambda p: p.name_en)
    items_html = ''.join(
        f"<tr><td style='padding:4px 8px'>{name(item.product)}</td>"
        f"<td style='padding:4px 8px;text-align:center'>{int(item.quantity)}</td>"
        f"<td style='padding:4px 8px;text-align:right'>৳{_fmt(item.line_total)}</td></tr>"
        for item in order.items.select_related('product').all()
    )
    headers = ('পণ্য', 'পরিমাণ', 'মোট') if is_bn else ('Product', 'Qty', 'Total')
    discount_amount = Decimal(str(order.discount_amount or 0))
    delivery_charge = Decimal(str(order.delivery_charge or 0))

    totals_rows = ''
    if discount_amount > 0:
        product_total = Decimal(str(order.subtotal)) + discount_amount
        totals_rows += (
            f"<tr><td colspan='2' style='padding:4px 8px;text-align:right;color:#6b7280'>{'পণ্য মূল্য' if is_bn else 'Product Total'}</td>"
            f"<td style='padding:4px 8px;text-align:right'>৳{_fmt(product_total)}</td></tr>"
        )
        totals_rows += (
            f"<tr><td colspan='2' style='padding:4px 8px;text-align:right;color:#166534'>{'ছাড়' if is_bn else 'Discount'}</td>"
            f"<td style='padding:4px 8px;text-align:right;color:#166534'>−৳{_fmt(discount_amount)}</td></tr>"
        )
        totals_rows += (
            f"<tr><td colspan='2' style='padding:4px 8px;text-align:right;color:#6b7280'>{'সাবটোটাল' if is_bn else 'Subtotal'}</td>"
            f"<td style='padding:4px 8px;text-align:right'>৳{_fmt(order.subtotal)}</td></tr>"
        )
    if delivery_charge > 0:
        totals_rows += (
            f"<tr><td colspan='2' style='padding:4px 8px;text-align:right;color:#6b7280'>{'ডেলিভারি চার্জ' if is_bn else 'Delivery Charge'}</td>"
            f"<td style='padding:4px 8px;text-align:right'>৳{_fmt(delivery_charge)}</td></tr>"
        )

    grand_label = 'সর্বমোট' if is_bn else 'Grand Total'
    return f"""
    <table style='width:100%;border-collapse:collapse;font-size:13px'>
      <thead>
        <tr style='background:#fef3c7'>
          <th style='padding:6px 8px;text-align:left'>{headers[0]}</th>
          <th style='padding:6px 8px'>{headers[1]}</th>
          <th style='padding:6px 8px;text-align:right'>{headers[2]}</th>
        </tr>
      </thead>
      <tbody>{items_html}</tbody>
      <tfoot>
        {totals_rows}
        <tr><td colspan='3' style='padding:8px;text-align:right;font-weight:bold;border-top:1px solid #e5e7eb'>
          {grand_label}: ৳{_fmt(order.grand_total)}
        </td></tr>
      </tfoot>
    </table>
    """


def _cashback_note_html(order, is_bn: bool) -> str:
    cb = Decimal(str(order.cashback_amount or 0))
    if order.is_guest or not order.customer_id or cb <= 0:
        return ''
    if is_bn:
        return f"""
        <p style='background:#ecfdf5;border:1px solid #a7f3d0;color:#166534;padding:10px 14px;border-radius:8px;font-size:13px;margin-top:16px'>
          ★ এই অর্ডারে আপনি <strong>৳{_fmt(cb)}</strong> ক্যাশব্যাক অর্জন করেছেন — আপনার অ্যাকাউন্টে যোগ হয়েছে, পরবর্তী অর্ডারে ব্যবহার করুন।
        </p>
        """
    return f"""
    <p style='background:#ecfdf5;border:1px solid #a7f3d0;color:#166534;padding:10px 14px;border-radius:8px;font-size:13px;margin-top:16px'>
      ★ You earned <strong>৳{_fmt(cb)}</strong> cashback on this order — added to your account, redeemable on your next purchase.
    </p>
    """


def _referral_share_block(code: str, is_bn: bool) -> str:
    if not code:
        return ''
    if is_bn:
        return f"""
        <p style='background:#fffbeb;border:1px solid #fde68a;color:#92400e;padding:10px 14px;border-radius:8px;font-size:13px;margin-top:12px'>
          🎁 আপনার রেফারেল কোড <strong>{code}</strong> বন্ধুদের সাথে শেয়ার করুন — তারা অর্ডার করলে আপনি রেফারেল বোনাস পাবেন!
        </p>
        """
    return f"""
    <p style='background:#fffbeb;border:1px solid #fde68a;color:#92400e;padding:10px 14px;border-radius:8px;font-size:13px;margin-top:12px'>
      🎁 Share your referral code <strong>{code}</strong> with friends — you'll earn a referral bonus when they order!
    </p>
    """


def _referral_share_html(order, is_bn: bool) -> str:
    if order.is_guest or not order.customer_id:
        return ''
    return _referral_share_block(order.customer.referral_code, is_bn)


def _base_html(title, body_content):
    return f"""
    <div style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden'>
      <div style='background:#f59e0b;padding:20px 24px'>
        <h1 style='color:#fff;margin:0;font-size:20px'>পূজারিঘর — PujariGhar</h1>
      </div>
      <div style='padding:24px'>
        <h2 style='color:#1f2937;margin-top:0'>{title}</h2>
        {body_content}
      </div>
      <div style='background:#f9fafb;padding:12px 24px;text-align:center;font-size:12px;color:#9ca3af'>
        পূজারিঘর | PujariGhar — All your puja essentials in one place.
      </div>
    </div>
    """


# ── Public API ────────────────────────────────────────────────────────────────

def send_password_reset(user, reset_link: str):
    if not user.email:
        return
    body = _base_html(
        "পাসওয়ার্ড রিসেট করুন — Reset Your Password",
        f"""
        <p>আপনি আপনার পাসওয়ার্ড রিসেট করার অনুরোধ করেছেন।<br>
        We received a request to reset your password.</p>
        <p style='text-align:center;margin:24px 0'>
          <a href='{reset_link}' style='display:inline-block;padding:12px 28px;background:#f59e0b;color:#fff;text-decoration:none;border-radius:8px;font-weight:bold'>
            পাসওয়ার্ড রিসেট করুন / Reset Password
          </a>
        </p>
        <p style='color:#6b7280;font-size:12px;margin-top:16px'>
        এই লিংকটি ৩০ মিনিটের জন্য বৈধ। যদি আপনি এই অনুরোধ না করেন, এই ইমেইলটি উপেক্ষা করুন।<br>
        This link is valid for 30 minutes. If you didn't request this, you can safely ignore this email.
        </p>
        """
    )
    _send_async("[PujariGhar] Reset Your Password", body, [user.email])


def send_welcome(user):
    if not user.email:
        return
    is_bn = user.preferred_language == 'bn'

    if is_bn:
        title = "পূজারিঘরে স্বাগতম! — Welcome to PujariGhar"
        intro = "আপনার অ্যাকাউন্ট সফলভাবে তৈরি হয়েছে। পূজারিঘরে আপনাকে স্বাগতম!"
        outro = "কেনাকাটা শুরু করুন এবং পূজার সকল প্রয়োজনীয় সামগ্রী এক জায়গায় পান।"
    else:
        title = "Welcome to PujariGhar!"
        intro = "Your account has been created successfully. Welcome to PujariGhar!"
        outro = "Start shopping and get all your puja essentials in one place."

    body = _base_html(
        title,
        f"""
        <p>{intro}</p>
        {_referral_share_block(user.referral_code, is_bn)}
        <p style='color:#6b7280;font-size:12px;margin-top:16px'>{outro}</p>
        """
    )
    _send_async("[PujariGhar] Welcome to PujariGhar!", body, [user.email])


def send_order_created(order):
    customer_email = _customer_email(order)
    admins = _admin_emails()
    is_bn = _customer_lang(order) == 'bn'

    # Customer mail — single language, based on customer's preferred_language
    if customer_email:
        if is_bn:
            title = f"অর্ডার নিশ্চিত হয়েছে — Order Confirmed #{order.order_number}"
            intro = "আপনার অর্ডার সফলভাবে গ্রহণ করা হয়েছে।"
            outro = "আপনার অর্ডার শীঘ্রই প্রস্তুত করা হবে। ধন্যবাদ!"
        else:
            title = f"Order Confirmed #{order.order_number}"
            intro = "Your order has been placed successfully."
            outro = "Your order will be prepared shortly. Thank you!"
        body = _base_html(
            title,
            f"""
            <p>{intro}</p>
            <p><strong>Order #:</strong> {order.order_number}<br>
            <strong>Payment:</strong> {order.payment_method} — {order.payment_status}</p>
            {_order_summary_html(order, is_bn)}
            <p style='color:#6b7280;font-size:12px;margin-top:16px'>{outro}</p>
            """
        )
        _send_async(f"[PujariGhar] Order #{order.order_number} Confirmed", body, [customer_email])

    # Admin mail
    if admins:
        body = _base_html(
            f"New Order #{order.order_number}",
            f"""
            <p>A new order has been placed.</p>
            <p><strong>Order #:</strong> {order.order_number}<br>
            <strong>Customer:</strong> {_customer_display(order)}<br>
            <strong>Payment:</strong> {order.payment_method} — {order.payment_status}</p>
            {_order_summary_html(order, False)}
            """
        )
        _send_async(f"[PujariGhar] New Order #{order.order_number}", body, admins)


def send_order_cancelled(order):
    customer_email = _customer_email(order)
    admins = _admin_emails()
    is_bn = _customer_lang(order) == 'bn'

    if customer_email:
        if is_bn:
            title = f"অর্ডার বাতিল হয়েছে — Order Cancelled #{order.order_number}"
            intro = "আপনার অর্ডার বাতিল করা হয়েছে।"
            outro = "কোনো সমস্যা হলে আমাদের সাথে যোগাযোগ করুন।"
        else:
            title = f"Order Cancelled #{order.order_number}"
            intro = "Your order has been cancelled."
            outro = "If you have any questions, please contact us."
        body = _base_html(
            title,
            f"""
            <p>{intro}</p>
            <p><strong>Order #:</strong> {order.order_number}</p>
            {_order_summary_html(order, is_bn)}
            <p style='color:#6b7280;font-size:12px;margin-top:16px'>{outro}</p>
            """
        )
        _send_async(f"[PujariGhar] Order #{order.order_number} Cancelled", body, [customer_email])

    if admins:
        body = _base_html(
            f"Order Cancelled #{order.order_number}",
            f"""
            <p>An order has been cancelled.</p>
            <p><strong>Order #:</strong> {order.order_number}<br>
            <strong>Customer:</strong> {_customer_display(order)}</p>
            {_order_summary_html(order, False)}
            """
        )
        _send_async(f"[PujariGhar] Order #{order.order_number} Cancelled", body, admins)


def send_order_delivered(order):
    customer_email = _customer_email(order)
    admins = _admin_emails()
    is_bn = _customer_lang(order) == 'bn'

    if customer_email:
        if is_bn:
            title = f"অর্ডার ডেলিভারি হয়েছে — Order Delivered #{order.order_number}"
            intro = "আপনার অর্ডার সফলভাবে ডেলিভারি হয়েছে। ধন্যবাদ!"
            outro = "আমাদের পণ্য পেয়ে আপনি সন্তুষ্ট হলে রিভিউ দিন।"
        else:
            title = f"Order Delivered #{order.order_number}"
            intro = "Your order has been delivered successfully. Thank you!"
            outro = "We'd love to hear your feedback — please leave a review!"
        body = _base_html(
            title,
            f"""
            <p>{intro}</p>
            <p><strong>Order #:</strong> {order.order_number}</p>
            {_order_summary_html(order, is_bn)}
            {_cashback_note_html(order, is_bn)}
            {_referral_share_html(order, is_bn)}
            <p style='color:#6b7280;font-size:12px;margin-top:16px'>{outro}</p>
            """
        )
        _send_async(f"[PujariGhar] Order #{order.order_number} Delivered", body, [customer_email])

    if admins:
        body = _base_html(
            f"Order Delivered #{order.order_number}",
            f"""
            <p>An order has been delivered.</p>
            <p><strong>Order #:</strong> {order.order_number}<br>
            <strong>Customer:</strong> {_customer_display(order)}</p>
            {_order_summary_html(order, is_bn)}
            """
        )
        _send_async(f"[PujariGhar] Order #{order.order_number} Delivered", body, admins)


# ── Promotional / marketing emails ──────────────────────────────────────────────

_PROMO_PREF_FIELD = {
    'NEW_PRODUCT': 'profile__notify_new_product',
    'NEW_PACKAGE': 'profile__notify_new_package',
    'OFFER':       'profile__notify_offers',
}


def promo_recipients_by_language(email_type: str):
    """Returns {'bn': [emails], 'en': [emails]} based on each user's preferred_language."""
    qs = (
        User.objects.filter(role__code='CUSTOMER', is_active=True, profile__notify_marketing=True)
        .exclude(email='')
    )
    field = _PROMO_PREF_FIELD.get(email_type)
    if field:
        qs = qs.filter(**{field: True})
    qs = qs.values_list('email', 'preferred_language').distinct()

    grouped = {'bn': [], 'en': []}
    for email, lang in qs:
        grouped['bn' if lang == 'bn' else 'en'].append(email)
    return grouped


def promo_recipients(email_type: str):
    grouped = promo_recipients_by_language(email_type)
    return grouped['bn'] + grouped['en']


def _send_promo_batch(conn, from_email, subject, html, recipients):
    BATCH_SIZE = 40  # BCC in batches: hides recipients from each other, stays under SMTP limits
    for i in range(0, len(recipients), BATCH_SIZE):
        batch = recipients[i:i + BATCH_SIZE]
        msg = EmailMultiAlternatives(
            subject,
            _html_to_text(html),
            from_email,
            [from_email],
            bcc=batch,
            connection=conn,
        )
        msg.attach_alternative(html, 'text/html')
        msg.send()
        logger.info(f"Promo mail batch sent: subject={subject!r} batch_size={len(batch)}")


def send_promo_email(promo):
    """Sends a PromoEmail campaign in the background, one single-language email per
    recipient's preferred_language, and updates its status when done."""
    def _send():
        close_old_connections()
        try:
            grouped = promo_recipients_by_language(promo.email_type)
            total = len(grouped['bn']) + len(grouped['en'])
            logger.info(f"Promo mail queued: id={promo.id} type={promo.email_type} recipients={total}")
            if not total:
                logger.warning(f"Promo mail failed (no recipients): id={promo.id} type={promo.email_type}")
                promo.status = 'FAILED'
                promo.recipient_count = 0
                promo.save(update_fields=['status', 'recipient_count'])
                return

            conn, from_email = _get_connection()
            if not conn:
                logger.warning(f"Promo mail failed (no SMTP configured): id={promo.id}")
                promo.status = 'FAILED'
                promo.recipient_count = total
                promo.save(update_fields=['status', 'recipient_count'])
                return

            if grouped['bn']:
                html = _base_html(promo.subject_bn, f"<p>{promo.message_bn.replace(chr(10), '<br>')}</p>")
                _send_promo_batch(conn, from_email, f"[PujariGhar] {promo.subject_bn}", html, grouped['bn'])

            if grouped['en']:
                html = _base_html(promo.subject_en, f"<p>{promo.message_en.replace(chr(10), '<br>')}</p>")
                _send_promo_batch(conn, from_email, f"[PujariGhar] {promo.subject_en}", html, grouped['en'])

            promo.status = 'SENT'
            promo.recipient_count = total
            promo.sent_at = timezone.now()
            promo.save(update_fields=['status', 'recipient_count', 'sent_at'])
            logger.info(f"Promo mail sent: id={promo.id} type={promo.email_type} recipients={total}")
        except Exception as e:
            logger.error(f"Promo mail send error: id={promo.id} error={e}", exc_info=True)
            promo.status = 'FAILED'
            promo.save(update_fields=['status'])
    threading.Thread(target=_send, daemon=True).start()
