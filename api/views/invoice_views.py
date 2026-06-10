import os
import logging
from decimal import Decimal

from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from weasyprint import HTML as WeasyHTML
from weasyprint.text.fonts import FontConfiguration

from api.models import SalesOrder
from api.utils.response import ApiResponse

logger    = logging.getLogger(__name__)
_FONT_DIR = os.path.join(os.path.dirname(__file__), '..', 'fonts')
_FONT_URL = f"file://{os.path.abspath(os.path.join(_FONT_DIR, 'NotoSansBengali-Regular.ttf'))}"
_LOGO_URL = f"file://{os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'assets', 'logo.png'))}"

SHOP_NAME    = 'পূজারিঘর'
SHOP_NAME_EN = 'PujariGhar'
SHOP_PHONE   = '01XXXXXXXXX'
SHOP_WEB     = 'pujarighar.com'
SHOP_ADDRESS = 'Dhaka, Bangladesh'


def _fmt(value) -> str:
    try:
        v = Decimal(str(value))
        return f'{v:,.2f}'
    except Exception:
        return '0.00'


def _build_html(order: SalesOrder, lang: str) -> str:
    isBn = lang == 'bn'

    def t(bn: str, en: str) -> str:
        return bn if isBn else en

    order_date   = order.created_at.strftime('%d/%m/%Y')
    invoice_date = order.created_at.strftime('%d/%m/%Y')

    name  = (order.shipping_name_bn if isBn else order.shipping_name_en) or order.shipping_name_bn or order.shipping_name_en
    addr  = (order.shipping_address_bn if isBn else order.shipping_address_en) or ''
    location_parts = [p for p in [order.shipping_district, order.shipping_thana, order.shipping_post_code] if p]
    location = ', '.join(location_parts)

    # ── Item rows ──────────────────────────────────────────────────────────────
    rows_html = ''
    for i, item in enumerate(order.items.all(), 1):
        item_name  = (item.product_name_bn if isBn else item.product_name_en) or item.product_name_bn
        qty        = int(float(item.quantity))
        unit_price = Decimal(str(item.unit_price))
        line_total = Decimal(str(item.line_total))
        is_pkg     = item.product.is_package
        pkg_badge  = f'<span class="pkg-badge">{t("প্যাকেজ","Pkg")}</span>' if is_pkg else ''

        rows_html += f"""
        <tr class="{'alt' if i % 2 == 0 else ''}">
            <td class="center">{i}</td>
            <td>{pkg_badge}{item_name}</td>
            <td class="center">{qty}</td>
            <td class="right">{_fmt(unit_price)}</td>
            <td class="right">{_fmt(line_total)}</td>
        </tr>"""

        if is_pkg:
            for pi in item.product.package_items.all():
                comp      = pi.component
                comp_name = (comp.name_bn if isBn else comp.name_en) or comp.name_bn
                comp_qty  = int(float(pi.quantity)) * qty
                rows_html += f"""
        <tr class="pkg-sub">
            <td></td>
            <td class="sub-label">↳ {comp_name}</td>
            <td class="center sub-label">{comp_qty}</td>
            <td></td><td></td>
        </tr>"""

    # ── Totals ─────────────────────────────────────────────────────────────────
    totals_html = f"""
        <tr>
            <td class="label">{t('সাবটোটাল','Subtotal')}</td>
            <td class="right">{_fmt(order.subtotal)}</td>
        </tr>"""

    if Decimal(str(order.discount_amount)) > 0:
        totals_html += f"""
        <tr class="green-row">
            <td class="label">{t('ছাড়','Discount')}</td>
            <td class="right">− {_fmt(order.discount_amount)}</td>
        </tr>"""

    if Decimal(str(order.delivery_charge)) > 0:
        totals_html += f"""
        <tr>
            <td class="label">{t('ডেলিভারি চার্জ','Delivery Charge')}</td>
            <td class="right">{_fmt(order.delivery_charge)}</td>
        </tr>"""

    if Decimal(str(order.tax_amount)) > 0:
        totals_html += f"""
        <tr>
            <td class="label">{t('কর','Tax')}</td>
            <td class="right">{_fmt(order.tax_amount)}</td>
        </tr>"""

    paid_label = t('(পরিশোধিত)', '(Paid)') if order.payment_status == 'PAID' else ''
    totals_html += f"""
        <tr class="grand-row">
            <td class="label bold">{t('সর্বমোট','Amount Payable')}</td>
            <td class="right bold">{_fmt(order.grand_total)} {paid_label}</td>
        </tr>"""

    # ── Cashback section ───────────────────────────────────────────────────────
    cashback_html = ''
    cb = Decimal(str(order.cashback_amount))
    if cb > 0 and not order.is_guest:
        cashback_html = f"""
<div class="cashback-box">
    <p class="cashback-title">{t(f'৳{_fmt(cb)} ক্যাশব্যাক পেয়েছেন এই অর্ডারে!', f'৳{_fmt(cb)} Cashback Rewarded For This Order')}</p>
    <p class="cashback-note">* {t('এই ক্যাশব্যাক আপনার পরবর্তী অর্ডারে প্রযোজ্য হবে', 'This cashback will be applicable to your next order')}</p>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<style>
  @font-face {{
    font-family: 'NotoSansBengali';
    src: url('{_FONT_URL}') format('truetype');
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'NotoSansBengali', Arial, sans-serif;
    font-size: 10pt;
    color: #111827;
    background: #fff;
    padding: 14mm 16mm;
  }}

  /* ── Header ── */
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding-bottom: 5mm;
    margin-bottom: 5mm;
    border-bottom: 0.4mm solid #e5e7eb;
  }}
  .logo-wrap img {{ height: 12mm; width: auto; object-fit: contain; }}
  .invoice-label {{ text-align: right; }}
  .invoice-title {{ font-size: 18pt; font-weight: bold; color: #111; letter-spacing: 0.5mm; }}
  .invoice-sub   {{ font-size: 9pt; color: #6b7280; margin-top: 1mm; }}

  /* ── Order meta row ── */
  .meta-row {{
    display: flex;
    gap: 6mm;
    font-size: 9pt;
    border: 0.3mm solid #e5e7eb;
    border-radius: 2mm;
    padding: 3mm 4mm;
    margin-bottom: 5mm;
  }}
  .meta-row .meta-item {{ flex: 1; }}
  .meta-row .meta-item strong {{ font-weight: 600; color: #111; }}
  .meta-row .meta-item span  {{ color: #6b7280; }}

  /* ── Bill from / to ── */
  .billing {{
    display: flex;
    gap: 6mm;
    margin-bottom: 5mm;
    border: 0.3mm solid #e5e7eb;
    border-radius: 2mm;
    padding: 4mm;
  }}
  .billing-col {{ flex: 1; }}
  .billing-col .section-label {{
    font-size: 8pt;
    font-weight: bold;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 0.4mm;
    margin-bottom: 2mm;
  }}
  .billing-col .biz-name {{ font-size: 11pt; font-weight: bold; margin-bottom: 1mm; }}
  .billing-col .biz-info {{ font-size: 8.5pt; color: #6b7280; margin-bottom: 0.5mm; }}
  .billing-col .cust-name {{ font-size: 11pt; font-weight: bold; margin-bottom: 1mm; }}
  .divider {{ width: 0.3mm; background: #e5e7eb; margin: 0 2mm; }}

  /* ── Items table ── */
  table.items {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 5mm;
    font-size: 9.5pt;
  }}
  table.items thead tr {{
    background: #f9fafb;
    border-top: 0.4mm solid #d1d5db;
    border-bottom: 0.4mm solid #d1d5db;
  }}
  table.items thead th {{
    padding: 2.5mm 3mm;
    font-weight: 600;
    font-size: 8.5pt;
    color: #374151;
    text-transform: uppercase;
    letter-spacing: 0.2mm;
  }}
  table.items tbody td {{
    padding: 2.5mm 3mm;
    border-bottom: 0.2mm solid #f3f4f6;
    vertical-align: middle;
  }}
  table.items tbody tr.alt td {{ background: #f9fafb; }}
  table.items tbody tr.pkg-sub td {{ border-bottom: none; }}
  .sub-label {{ font-size: 8pt; color: #9ca3af; padding-top: 0.5mm !important; padding-bottom: 0.5mm !important; }}
  .center {{ text-align: center; }}
  .right  {{ text-align: right; }}
  .left   {{ text-align: left; }}
  .bold   {{ font-weight: bold; }}
  .green-row td {{ color: #16a34a; }}
  .pkg-badge {{
    font-size: 7pt;
    background: #fef3c7;
    color: #d97706;
    padding: 0.5mm 1.5mm;
    border-radius: 1.5mm;
    margin-right: 1.5mm;
  }}

  /* ── Bottom: totals right, space left ── */
  .bottom-section {{
    display: flex;
    gap: 6mm;
    align-items: flex-start;
  }}
  .bottom-left {{ flex: 1; }}
  .totals-table {{
    width: 70mm;
    border-collapse: collapse;
    font-size: 9.5pt;
    border: 0.3mm solid #e5e7eb;
    border-radius: 2mm;
    overflow: hidden;
  }}
  .totals-table td {{ padding: 2mm 4mm; border-bottom: 0.2mm solid #f3f4f6; }}
  .totals-table td.label {{ color: #6b7280; }}
  .totals-table tr.grand-row td {{
    border-top: 0.4mm solid #111;
    border-bottom: none;
    font-size: 10.5pt;
    padding-top: 3mm;
    padding-bottom: 3mm;
    color: #111;
  }}

  /* ── Cashback ── */
  .cashback-box {{
    margin-top: 8mm;
    text-align: center;
    padding: 4mm;
    border-top: 0.3mm dashed #d1d5db;
  }}
  .cashback-title {{ font-size: 13pt; font-weight: bold; color: #111; margin-bottom: 1.5mm; }}
  .cashback-note  {{ font-size: 8.5pt; color: #6b7280; font-style: italic; }}

  /* ── Footer ── */
  .footer {{
    margin-top: 8mm;
    border-top: 0.3mm solid #e5e7eb;
    padding-top: 3mm;
    text-align: center;
    font-size: 8pt;
    color: #9ca3af;
  }}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="logo-wrap">
    <img src="{_LOGO_URL}" alt="{SHOP_NAME_EN}" />
  </div>
  <div class="invoice-label">
    <div class="invoice-title">{t('চালান', 'TAX INVOICE')}</div>
    <div class="invoice-sub"># {order.order_number}</div>
  </div>
</div>

<!-- Order meta row -->
<div class="meta-row">
  <div class="meta-item"><span>{t('অর্ডার নং:', 'Order No:')}</span> <strong>{order.order_number}</strong></div>
  <div class="meta-item"><span>{t('অর্ডার তারিখ:', 'Order Date:')}</span> <strong>{order_date}</strong></div>
  <div class="meta-item"><span>{t('চালান তারিখ:', 'Invoice Date:')}</span> <strong>{invoice_date}</strong></div>
  <div class="meta-item"><span>{t('পেমেন্ট:', 'Payment:')}</span> <strong>{'COD' if order.payment_method == 'COD' else t('অনলাইন','Online')}</strong></div>
</div>

<!-- Bill From / Bill To -->
<div class="billing">
  <div class="billing-col">
    <div class="section-label">{t('প্রেরক', 'Bill From')}</div>
    <div class="biz-name">{SHOP_NAME_EN}</div>
    <div class="biz-info">{SHOP_PHONE}</div>
    <div class="biz-info">{SHOP_WEB}</div>
    <div class="biz-info">{SHOP_ADDRESS}</div>
  </div>
  <div class="divider"></div>
  <div class="billing-col">
    <div class="section-label">{t('গ্রাহক', 'Billed To')}</div>
    <div class="cust-name">{name}</div>
    <div class="biz-info">{order.shipping_phone}</div>
    {f'<div class="section-label" style="margin-top:2mm">{t("ডেলিভারি ঠিকানা","Deliver To")}</div>' if addr or location else ''}
    {f'<div class="biz-info">{addr}</div>' if addr else ''}
    {f'<div class="biz-info">{location}</div>' if location else ''}
  </div>
</div>

<!-- Items table -->
<table class="items">
  <thead>
    <tr>
      <th class="center" style="width:8mm">{t('ক্র.','SL')}</th>
      <th class="left">{t('পণ্য','Products')}</th>
      <th class="center" style="width:16mm">{t('পরিমাণ','Qty')}</th>
      <th class="right" style="width:26mm">{t('একক মূল্য','Unit Price')}</th>
      <th class="right" style="width:26mm">{t('মোট','Amount')}</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<!-- Bottom: left space + right totals -->
<div class="bottom-section">
  <div class="bottom-left"></div>
  <table class="totals-table">
    <tbody>
      {totals_html}
    </tbody>
  </table>
</div>

{cashback_html}

<div class="footer">
  {t('ক্রয়ের জন্য ধন্যবাদ — পূজারিঘর', 'Thank you for your purchase — PujariGhar')}
</div>

</body>
</html>"""


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_invoice(request, pk):
    try:
        order = SalesOrder.objects.prefetch_related(
            'items__product__package_items__component',
        ).get(pk=pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)

    lang        = request.query_params.get('lang', 'en')
    disposition = request.query_params.get('disposition', 'inline')

    try:
        html_str    = _build_html(order, lang)
        font_config = FontConfiguration()
        pdf         = WeasyHTML(string=html_str).write_pdf(font_config=font_config)
    except Exception as e:
        logger.error(f'Invoice PDF generation failed: {e}', exc_info=True)
        return ApiResponse(message='PDF generation failed', errors=str(e), status_code=500)

    response = HttpResponse(pdf, content_type='application/pdf')
    filename = f'invoice-{order.order_number}.pdf'
    response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    return response
