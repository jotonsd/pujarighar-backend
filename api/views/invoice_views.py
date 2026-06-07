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


def _fmt(value) -> str:
    return f'৳{Decimal(str(value)):,.0f}'


def _build_html(order: SalesOrder, lang: str) -> str:
    isBn = lang == 'bn'

    def t(bn: str, en: str) -> str:
        return bn if isBn else en

    date_str = order.created_at.strftime('%d %B %Y')

    payment_method = t(
        'ক্যাশ অন ডেলিভারি' if order.payment_method == 'COD' else 'অনলাইন পেমেন্ট',
        'Cash on Delivery'   if order.payment_method == 'COD' else 'Online Payment',
    )
    payment_status_label = t(
        'পরিশোধিত ✓' if order.payment_status == 'PAID' else 'অপরিশোধিত',
        'PAID ✓'     if order.payment_status == 'PAID' else 'UNPAID',
    )
    payment_status_class = 'paid' if order.payment_status == 'PAID' else 'unpaid'

    name = order.shipping_name_bn if isBn else order.shipping_name_en
    addr = order.shipping_address_bn if isBn else order.shipping_address_en

    location_parts = []
    if order.shipping_district:
        location_parts.append(order.shipping_district)
    if order.shipping_thana:
        location_parts.append(order.shipping_thana)
    if order.shipping_post_code:
        location_parts.append(order.shipping_post_code)
    location = ', '.join(location_parts)

    rows_html = ''
    for i, item in enumerate(order.items.all(), 1):
        item_name = item.product_name_bn if isBn else item.product_name_en
        qty       = int(float(item.quantity))
        is_pkg    = item.product.is_package
        pkg_badge = f'<span class="pkg-badge">{t("প্যাকেজ", "Pkg")}</span>' if is_pkg else ''
        rows_html += f"""
        <tr class="{'alt' if i % 2 == 0 else ''}">
            <td class="center">{i}</td>
            <td>{pkg_badge}{item_name}</td>
            <td class="right">{qty}</td>
            <td class="right">{_fmt(item.unit_price)}</td>
            <td class="right bold">{_fmt(item.line_total)}</td>
        </tr>"""
        if is_pkg:
            for pkg_item in item.product.package_items.all():
                comp      = pkg_item.component
                comp_name = comp.name_bn if isBn else comp.name_en
                comp_qty  = int(float(pkg_item.quantity)) * qty
                rows_html += f"""
        <tr class="pkg-component-row">
            <td></td>
            <td class="pkg-component">↳ {comp_name}</td>
            <td class="right pkg-component">{comp_qty}</td>
            <td class="right pkg-component">—</td>
            <td class="right pkg-component">—</td>
        </tr>"""

    totals_html = f"""
        <tr>
            <td colspan="4" class="right gray">{t('সাবটোটাল', 'Subtotal')}</td>
            <td class="right">{_fmt(order.subtotal)}</td>
        </tr>"""

    if Decimal(str(order.discount_amount)) > 0:
        totals_html += f"""
        <tr>
            <td colspan="4" class="right green">{t('ছাড়', 'Discount')}</td>
            <td class="right green">− {_fmt(order.discount_amount)}</td>
        </tr>"""

    if Decimal(str(order.delivery_charge)) > 0:
        totals_html += f"""
        <tr>
            <td colspan="4" class="right gray">{t('ডেলিভারি চার্জ', 'Delivery Charge')}</td>
            <td class="right">{_fmt(order.delivery_charge)}</td>
        </tr>"""

    if Decimal(str(order.tax_amount)) > 0:
        totals_html += f"""
        <tr>
            <td colspan="4" class="right gray">{t('কর', 'Tax')}</td>
            <td class="right">{_fmt(order.tax_amount)}</td>
        </tr>"""

    totals_html += f"""
        <tr class="grand-total-row">
            <td colspan="4" class="right amber bold">{t('সর্বমোট', 'Grand Total')}</td>
            <td class="right amber bold">{_fmt(order.grand_total)}</td>
        </tr>"""

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
    font-size: 11pt;
    color: #111827;
    background: #fff;
    padding: 20mm 18mm;
  }}
  /* Header bar */
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 0.5mm solid #d97706;
    padding-bottom: 5mm;
    margin-bottom: 6mm;
  }}
  .shop-brand {{ display: flex; align-items: center; gap: 3mm; }}
  .shop-sub  {{ font-size: 8pt; color: #6b7280; }}
  .invoice-label {{ text-align: right; }}
  .invoice-title {{ font-size: 16pt; font-weight: bold; color: #d97706; }}
  .invoice-num   {{ font-size: 9pt; color: #6b7280; margin-top: 1mm; font-family: monospace; }}
  /* Meta row */
  .meta {{
    display: flex;
    justify-content: space-between;
    font-size: 9pt;
    color: #6b7280;
    margin-bottom: 6mm;
    border-bottom: 0.3mm solid #e5e7eb;
    padding-bottom: 3mm;
  }}
  .paid   {{ color: #16a34a; font-weight: bold; }}
  .unpaid {{ color: #d97706; font-weight: bold; }}
  /* Customer */
  .section-title {{
    font-size: 8pt;
    font-weight: bold;
    color: #d97706;
    text-transform: uppercase;
    letter-spacing: 0.5mm;
    margin-bottom: 2mm;
  }}
  .customer-name  {{ font-size: 13pt; font-weight: bold; margin-bottom: 1mm; }}
  .customer-info  {{ font-size: 9pt; color: #6b7280; margin-bottom: 1mm; }}
  /* Table */
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 6mm;
    font-size: 10pt;
  }}
  thead tr {{
    background: #fef3c7;
  }}
  thead th {{
    padding: 3mm 3mm;
    color: #d97706;
    font-weight: bold;
    font-size: 9pt;
    text-transform: uppercase;
    letter-spacing: 0.3mm;
  }}
  .th-left  {{ text-align: left; }}
  .th-right {{ text-align: right; }}
  .th-center{{ text-align: center; }}
  tbody tr td {{
    padding: 2.5mm 3mm;
    border-bottom: 0.2mm solid #f3f4f6;
    vertical-align: middle;
  }}
  tbody tr.alt td {{ background: #f9fafb; }}
  .center {{ text-align: center; color: #9ca3af; font-size: 9pt; }}
  .right  {{ text-align: right; }}
  .bold   {{ font-weight: bold; }}
  .gray   {{ color: #6b7280; }}
  .green  {{ color: #16a34a; }}
  .amber  {{ color: #d97706; }}
  .pkg-badge {{
    font-size: 7.5pt;
    background: #fef3c7;
    color: #d97706;
    padding: 0.5mm 1.5mm;
    border-radius: 2mm;
    margin-right: 1.5mm;
  }}
  /* Package component sub-rows */
  .pkg-component-row td {{ border-bottom: none; background: #f9fafb; }}
  .pkg-component {{
    font-size: 8.5pt;
    color: #6b7280;
    padding-top: 1mm !important;
    padding-bottom: 1mm !important;
  }}
  /* Totals */
  .totals-row td {{ border-bottom: none; padding: 1.5mm 3mm; }}
  .grand-total-row td {{
    border-top: 0.5mm solid #d97706;
    padding-top: 3mm;
    font-size: 12pt;
  }}
  /* Footer */
  .footer {{
    margin-top: 12mm;
    border-top: 0.3mm solid #e5e7eb;
    padding-top: 4mm;
    text-align: center;
    font-size: 8.5pt;
    color: #9ca3af;
  }}
</style>
</head>
<body>

<div class="header">
  <div class="shop-brand">
    <img src="{_LOGO_URL}" alt="PujariGhar" style="height:10mm; width:auto; object-fit:contain;" />
    <span class="shop-sub">pujarighar.com</span>
  </div>
  <div class="invoice-label">
    <div class="invoice-title">{t('চালান', 'Invoice')}</div>
    <div class="invoice-num"># {order.order_number}</div>
  </div>
</div>

<div class="meta">
  <span>{t('তারিখ:', 'Date:')} {date_str}</span>
  <span>{t('পেমেন্ট:', 'Payment:')} {payment_method}</span>
  <span class="{payment_status_class}">{payment_status_label}</span>
</div>

<div class="section-title">{t('গ্রাহকের তথ্য', 'Bill To')}</div>
<div class="customer-name">{name}</div>
<div class="customer-info">{order.shipping_phone}</div>
{f'<div class="customer-info">{addr}</div>' if addr else ''}
{f'<div class="customer-info">{location}</div>' if location else ''}

<table>
  <thead>
    <tr>
      <th class="th-center" style="width:8mm">#</th>
      <th class="th-left">{t('পণ্য', 'Item')}</th>
      <th class="th-right" style="width:18mm">{t('পরিমাণ', 'Qty')}</th>
      <th class="th-right" style="width:28mm">{t('একক মূল্য', 'Unit Price')}</th>
      <th class="th-right" style="width:28mm">{t('মোট', 'Total')}</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
  <tbody class="totals-row">
    {totals_html}
  </tbody>
</table>

<div class="footer">
  {t('ধন্যবাদ আপনার ক্রয়ের জন্য • পূজারিঘর', 'Thank you for your purchase • PujariGhar')}
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
