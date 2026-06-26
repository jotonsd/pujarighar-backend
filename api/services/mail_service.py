import logging
import re
import threading
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
        User.objects.filter(role='ADMIN', is_active=True)
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


def _order_summary_html(order):
    items_html = ''.join(
        f"<tr><td style='padding:4px 8px'>{item.product.name_bn}<br><span style='font-size:11px;color:#6b7280'>{item.product.name_en}</span></td>"
        f"<td style='padding:4px 8px;text-align:center'>{int(item.quantity)}</td>"
        f"<td style='padding:4px 8px;text-align:right'>৳{item.line_total}</td></tr>"
        for item in order.items.select_related('product').all()
    )
    return f"""
    <table style='width:100%;border-collapse:collapse;font-size:13px'>
      <thead>
        <tr style='background:#fef3c7'>
          <th style='padding:6px 8px;text-align:left'>Product</th>
          <th style='padding:6px 8px'>Qty</th>
          <th style='padding:6px 8px;text-align:right'>Total</th>
        </tr>
      </thead>
      <tbody>{items_html}</tbody>
      <tfoot>
        <tr><td colspan='3' style='padding:8px;text-align:right;font-weight:bold;border-top:1px solid #e5e7eb'>
          Grand Total: ৳{order.grand_total}
        </td></tr>
      </tfoot>
    </table>
    """


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


def send_order_created(order):
    customer_email = _customer_email(order)
    admins = _admin_emails()

    # Customer mail
    if customer_email:
        body = _base_html(
            f"অর্ডার নিশ্চিত হয়েছে — Order Confirmed #{order.order_number}",
            f"""
            <p>আপনার অর্ডার সফলভাবে গ্রহণ করা হয়েছে।<br>
            Your order has been placed successfully.</p>
            <p><strong>Order #:</strong> {order.order_number}<br>
            <strong>Payment:</strong> {order.payment_method} — {order.payment_status}</p>
            {_order_summary_html(order)}
            <p style='color:#6b7280;font-size:12px;margin-top:16px'>
            আপনার অর্ডার শীঘ্রই প্রস্তুত করা হবে। ধন্যবাদ!<br>
            Your order will be prepared shortly. Thank you!
            </p>
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
            <strong>Customer:</strong> {customer_email or 'Guest'}<br>
            <strong>Payment:</strong> {order.payment_method} — {order.payment_status}</p>
            {_order_summary_html(order)}
            """
        )
        _send_async(f"[PujariGhar] New Order #{order.order_number}", body, admins)


def send_order_cancelled(order):
    customer_email = _customer_email(order)
    admins = _admin_emails()

    if customer_email:
        body = _base_html(
            f"অর্ডার বাতিল হয়েছে — Order Cancelled #{order.order_number}",
            f"""
            <p>আপনার অর্ডার বাতিল করা হয়েছে।<br>
            Your order has been cancelled.</p>
            <p><strong>Order #:</strong> {order.order_number}</p>
            {_order_summary_html(order)}
            <p style='color:#6b7280;font-size:12px;margin-top:16px'>
            কোনো সমস্যা হলে আমাদের সাথে যোগাযোগ করুন।<br>
            If you have any questions, please contact us.
            </p>
            """
        )
        _send_async(f"[PujariGhar] Order #{order.order_number} Cancelled", body, [customer_email])

    if admins:
        body = _base_html(
            f"Order Cancelled #{order.order_number}",
            f"""
            <p>An order has been cancelled.</p>
            <p><strong>Order #:</strong> {order.order_number}<br>
            <strong>Customer:</strong> {customer_email or 'Guest'}</p>
            {_order_summary_html(order)}
            """
        )
        _send_async(f"[PujariGhar] Order #{order.order_number} Cancelled", body, admins)


def send_order_delivered(order):
    customer_email = _customer_email(order)
    admins = _admin_emails()

    if customer_email:
        body = _base_html(
            f"অর্ডার ডেলিভারি হয়েছে — Order Delivered #{order.order_number}",
            f"""
            <p>আপনার অর্ডার সফলভাবে ডেলিভারি হয়েছে। ধন্যবাদ!<br>
            Your order has been delivered successfully. Thank you!</p>
            <p><strong>Order #:</strong> {order.order_number}</p>
            {_order_summary_html(order)}
            <p style='color:#6b7280;font-size:12px;margin-top:16px'>
            আমাদের পণ্য পেয়ে আপনি সন্তুষ্ট হলে রিভিউ দিন।<br>
            We'd love to hear your feedback — please leave a review!
            </p>
            """
        )
        _send_async(f"[PujariGhar] Order #{order.order_number} Delivered", body, [customer_email])

    if admins:
        body = _base_html(
            f"Order Delivered #{order.order_number}",
            f"""
            <p>An order has been delivered.</p>
            <p><strong>Order #:</strong> {order.order_number}<br>
            <strong>Customer:</strong> {customer_email or 'Guest'}</p>
            {_order_summary_html(order)}
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
        User.objects.filter(role='CUSTOMER', is_active=True, profile__notify_marketing=True)
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
