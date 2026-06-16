import logging
import threading
from django.core.mail import EmailMessage, get_connection

from api.models import SiteSetting, User

logger = logging.getLogger(__name__)


def _get_connection():
    s = SiteSetting.get()
    if not s.email_host or not s.email_host_user:
        return None, None
    conn = get_connection(
        backend='django.core.mail.backends.smtp.EmailBackend',
        host=s.email_host,
        port=s.email_port,
        username=s.email_host_user,
        password=s.email_host_password,
        use_tls=s.email_use_tls,
        fail_silently=False,
    )
    from_email = s.email_default_from or s.email_host_user
    return conn, from_email


def _admin_emails():
    return list(
        User.objects.filter(role='ADMIN', is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )


def _send_async(subject, body, recipients):
    def _send():
        try:
            conn, from_email = _get_connection()
            if not conn or not recipients:
                return
            msg = EmailMessage(subject, body, from_email, recipients, connection=conn)
            msg.content_subtype = 'html'
            msg.send()
        except Exception as e:
            logger.error(f"Mail send error: {e}", exc_info=True)
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
