import base64
import io
import os
import logging
from decimal import Decimal

import qrcode
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from weasyprint import HTML as WeasyHTML
from weasyprint.text.fonts import FontConfiguration

from api.models import SalesOrder, SiteSetting
from api.utils.response import ApiResponse

logger    = logging.getLogger(__name__)
_FONT_DIR = os.path.join(os.path.dirname(__file__), '..', 'fonts')
_FONT_URL = f"file://{os.path.abspath(os.path.join(_FONT_DIR, 'NotoSansBengali-Regular.ttf'))}"
_LOGO_URL = f"file://{os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'assets', 'logo.png'))}"

SHOP_NAME_BN = 'পূজারিঘর'
SHOP_NAME_EN = 'PujariGhar'
SHOP_PHONE   = '01978604807'
SHOP_WEB     = 'pujarighar.com'
SHOP_ADDRESS = 'Dhaka, Bangladesh'


def _qr_data_uri(url: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=4, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _fmt(value) -> str:
    try:
        v = Decimal(str(value))
        return f'{v:,.2f}'
    except Exception:
        return '0.00'


PAGE_CSS = {
    'A4':      '@page { size: A4; margin: 0; }',
    'A5':      '@page { size: A5; margin: 0; }',
    'LETTER':  '@page { size: letter; margin: 0; }',
    'THERMAL': '@page { size: 80mm auto; margin: 0; }',
}

THERMAL_BODY_CSS = """
    body { padding: 4mm 5mm 4mm; font-size: 8pt; }
    .hdr { flex-direction: column; align-items: center; text-align: center; gap: 2mm; }
    .hdr-left, .hdr-inv { text-align: center; }
    .hdr-inv .iw { font-size: 14pt; }
    .billing { flex-direction: column; gap: 2mm; }
    .bill-col { text-align: left !important; }
    .bill-divider { display: none; }
    .items-table { font-size: 8pt; }
    .items-table thead th { font-size: 7pt; }
    .totals-wrap { flex-direction: column; align-items: flex-start; gap: 2mm; }
    .totals-table { width: 100%; }
    .totals-qr img { width: 18mm; height: 18mm; }
    .footer { flex-direction: column; gap: 1mm; font-size: 7.5pt; }
"""


def _build_html(order: SalesOrder, lang: str, is_admin: bool = False, page_size: str = 'A5') -> str:
    isBn = lang == 'bn'

    def t(bn: str, en: str) -> str:
        return bn if isBn else en

    order_date = order.created_at.strftime('%d %b %Y')
    name       = (order.shipping_name_bn if isBn else order.shipping_name_en) or order.shipping_name_bn or order.shipping_name_en
    addr       = (order.shipping_address_bn if isBn else order.shipping_address_en) or ''
    location_parts = [p for p in [order.shipping_district, order.shipping_thana, order.shipping_post_code] if p]
    location   = ', '.join(location_parts)

    discount_amount    = Decimal(str(order.discount_amount or 0))
    has_order_discount = discount_amount > 0
    show_cashback      = not order.is_guest and (is_admin or order.status == 'DELIVERED')

    payment_label = t('ক্যাশ অন ডেলিভারি', 'Cash on Delivery') if order.payment_method == 'COD' else t('অনলাইন', 'Online')
    paid_stamp    = f'<span class="stamp-paid">{t("পরিশোধিত","PAID")}</span>' if order.payment_status == 'PAID' else f'<span class="stamp-unpaid">{t("অপরিশোধিত","UNPAID")}</span>'

    # ── Item rows ─────────────────────────────────────────────────────────────
    rows_html = ''
    for i, item in enumerate(order.items.all(), 1):
        item_name  = (item.product_name_bn if isBn else item.product_name_en) or item.product_name_bn
        qty        = int(float(item.quantity))
        unit_price = Decimal(str(item.unit_price))
        line_total = Decimal(str(item.line_total))
        orig_unit  = Decimal(str(item.original_unit_price)) if item.original_unit_price else unit_price
        is_pkg     = item.product.is_package

        pkg_badge = f'<span class="pkg-badge">{t("প্যাকেজ","PKG")}</span> ' if is_pkg else ''

        if orig_unit > unit_price:
            unit_cell = f'<s class="old-p">{_fmt(orig_unit)}</s> {_fmt(unit_price)}'
        else:
            unit_cell = _fmt(unit_price)
        amount_cell = _fmt(line_total)

        row_class = 'row-alt' if i % 2 == 0 else ''
        rows_html += f"""
        <tr class="{row_class}">
            <td class="tc sl">{i}</td>
            <td>{pkg_badge}{item_name}</td>
            <td class="tc">{qty}</td>
            <td class="tr">{unit_cell}</td>
            <td class="tr">{amount_cell}</td>
        </tr>"""

        if is_pkg:
            for pi in item.product.package_items.all():
                comp      = pi.component
                comp_name = (comp.name_bn if isBn else comp.name_en) or comp.name_bn
                comp_qty  = int(float(pi.quantity)) * qty
                rows_html += f"""
        <tr class="row-sub">
            <td></td>
            <td class="sub-item">↳ {comp_name}</td>
            <td class="tc sub-item">{comp_qty}</td>
            <td></td><td></td>
        </tr>"""

    # ── Totals rows ───────────────────────────────────────────────────────────
    totals_html = f"""
        <tr class="tot-row">
            <td class="tot-label">{t('সাবটোটাল', 'Subtotal')}</td>
            <td class="tot-val">৳ {_fmt(order.subtotal)}</td>
        </tr>"""

    if has_order_discount:
        totals_html += f"""
        <tr class="tot-row disc-row">
            <td class="tot-label">{t('ছাড়', 'Discount')}</td>
            <td class="tot-val">− ৳ {_fmt(order.discount_amount)}</td>
        </tr>"""

    if Decimal(str(order.delivery_charge)) > 0:
        totals_html += f"""
        <tr class="tot-row">
            <td class="tot-label">{t('ডেলিভারি চার্জ', 'Delivery Charge')}</td>
            <td class="tot-val">৳ {_fmt(order.delivery_charge)}</td>
        </tr>"""

    if Decimal(str(order.tax_amount)) > 0:
        totals_html += f"""
        <tr class="tot-row">
            <td class="tot-label">{t('কর', 'Tax')}</td>
            <td class="tot-val">৳ {_fmt(order.tax_amount)}</td>
        </tr>"""

    if Decimal(str(getattr(order, 'cashback_used', 0) or 0)) > 0:
        totals_html += f"""
        <tr class="tot-row disc-row">
            <td class="tot-label">{t('ক্যাশব্যাক ব্যবহৃত', 'Cashback Used')}</td>
            <td class="tot-val">− ৳ {_fmt(order.cashback_used)}</td>
        </tr>"""

    totals_html += f"""
        <tr class="grand-row">
            <td class="tot-label"><b>{t('সর্বমোট', 'Total Payable')}</b></td>
            <td class="tot-val grand-val">৳ {_fmt(order.grand_total)}</td>
        </tr>"""

    # ── Below-totals messages ─────────────────────────────────────────────────
    msg_parts = []
    if has_order_discount:
        msg_parts.append(f'<span class="msg-savings">✓ {t(f"আপনি মোট ৳{_fmt(order.discount_amount)} সাশ্রয় করেছেন এই অর্ডারে।", f"You saved ৳{_fmt(order.discount_amount)} on this order.")}</span>')

    cb = Decimal(str(getattr(order, 'cashback_amount', 0) or 0))
    if cb > 0 and show_cashback:
        msg_parts.append(f'<span class="msg-cashback">★ {t(f"এই অর্ডারে ৳{_fmt(cb)} ক্যাশব্যাক অর্জিত — পরবর্তী অর্ডারে প্রযোজ্য।", f"৳{_fmt(cb)} cashback earned on this order — redeemable on next purchase.")}</span>')

    messages_html = ''
    if msg_parts:
        messages_html = '<div class="msg-block">' + ''.join(msg_parts) + '</div>'

    notes_html = ''
    note_text = (order.notes_bn if isBn else order.notes_en) or ''
    if note_text.strip():
        notes_html = f'<div class="note-block"><b>{t("নোট:","Note:")}</b> {note_text}</div>'

    qr_uri = _qr_data_uri('https://pujarighar.com')

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<style>
  @font-face {{
    font-family: 'NotoSansBengali';
    src: url('{_FONT_URL}') format('truetype');
  }}
  {PAGE_CSS.get(page_size, PAGE_CSS['A5'])}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'NotoSansBengali', Arial, sans-serif;
    font-size: 9.5pt;
    color: #1c1c1c;
    background: #fff;
    padding: 5mm 8mm 4mm;
  }}
  {THERMAL_BODY_CSS if page_size == 'THERMAL' else ''}

  /* ── Top header ── */
  .hdr {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 2mm;
    border-bottom: 0.3mm dotted #aaa;
    margin-bottom: 2mm;
  }}
  .hdr-left {{ display: flex; align-items: center; }}
  .hdr-shop img  {{ height: 11mm; width: auto; object-fit: contain; display: block; }}
  .hdr-inv       {{ text-align: right; }}
  .hdr-inv .iw   {{ font-size: 20pt; font-weight: bold; letter-spacing: 1mm; color: #1c1c1c; line-height: 1; }}
  .hdr-inv .in   {{ font-size: 8.5pt; color: #888; margin-top: 1mm; }}

  /* ── Meta strip ── */
  .meta-strip {{
    display: flex;
    justify-content: space-between;
    margin-bottom: 2mm;
    align-items: baseline;
  }}
  .meta-cell {{ white-space: nowrap; }}
  .meta-cell .mc-lbl {{ font-size: 8.5pt; color: #999; }}
  .meta-cell .mc-val {{ font-size: 8.5pt; color: #1c1c1c; font-weight: 600; }}
  .stamp-paid   {{ font-size: 7.5pt; font-weight: bold; color: #166534; border: 0.25mm dotted #166534; padding: 0.2mm 1.5mm; border-radius: 0.8mm; }}
  .stamp-unpaid {{ font-size: 7.5pt; font-weight: bold; color: #92400e; border: 0.25mm dotted #92400e; padding: 0.2mm 1.5mm; border-radius: 0.8mm; }}

  /* ── Billing ── */
  .billing {{
    display: flex;
    gap: 5mm;
    margin-bottom: 2mm;
    border-top: 0.25mm dotted #aaa;
    border-bottom: 0.25mm dotted #aaa;
    padding: 1.5mm 0;
  }}
  .bill-col {{ flex: 1; }}
  .bill-col .bc-lbl  {{ font-size: 7pt; text-transform: uppercase; letter-spacing: 0.4mm; color: #999; font-weight: bold; margin-bottom: 1mm; }}
  .bill-col .bc-name {{ font-size: 10pt; font-weight: bold; color: #1c1c1c; margin-bottom: 0.5mm; }}
  .bill-col .bc-info {{ font-size: 8.5pt; color: #555; line-height: 1.5; }}
  .bill-divider {{ width: 0.25mm; background: #ddd; }}

  /* ── Items table ── */
  .items-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 7pt;
  }}
  .items-table thead tr {{
    border-top: 0.3mm dotted #aaa;
    border-bottom: 0.3mm dotted #aaa;
  }}
  .items-table thead th {{
    padding: 1mm 1.5mm;
    font-size: 6.5pt;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.2mm;
    color: #333;
  }}
  .items-table tbody td {{
    padding: 0.8mm 1.5mm;
    border-bottom: 0.2mm dotted #eee;
    vertical-align: top;
  }}
  .row-alt td {{ background: none; }}
  .row-sub td {{ border-bottom: none; }}
  .sub-item  {{ font-size: 6.5pt; color: #aaa; padding-top: 0.1mm !important; padding-bottom: 0.1mm !important; }}
  .sl        {{ font-size: 6.5pt; color: #bbb; }}
  .tc {{ text-align: center; }}
  .tr {{ text-align: right; }}
  .pkg-badge {{
    font-size: 6.5pt; font-weight: bold;
    border: 0.2mm dotted #bbb; color: #777;
    padding: 0.2mm 1.2mm; border-radius: 0.6mm;
  }}
  .old-p {{ color: #bbb; text-decoration: line-through; font-size: 6pt; }}

  /* ── Totals ── */
  .totals-wrap {{
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    border-top: 0.3mm dotted #aaa;
    padding-top: 2mm;
    margin-top: 0.5mm;
  }}
  .totals-qr img {{ width: 22mm; height: 22mm; display: block; }}
  .totals-qr p   {{ font-size: 7pt; color: #aaa; text-align: center; margin-top: 1mm; }}
  .totals-table {{
    width: 72mm;
    border-collapse: collapse;
    font-size: 9.5pt;
  }}
  .tot-row td {{ padding: 1.2mm 3mm; border-bottom: 0.2mm dotted #ddd; }}
  .tot-label {{ color: #555; text-align: right; }}
  .tot-val   {{ text-align: right; color: #222; white-space: nowrap; padding-left: 6mm; }}
  .disc-row td {{ color: #166534; font-weight: 600; }}
  .grand-row td {{
    border-top: 0.3mm dotted #aaa;
    border-bottom: none;
    padding: 2mm 3mm;
  }}
  .grand-val {{ font-weight: bold; font-size: 10.5pt; color: #1c1c1c; }}

  /* ── Messages ── */
  .msg-block {{
    margin-top: 2.5mm;
    text-align: center;
    border-top: 0.25mm dotted #ccc;
    padding-top: 2.5mm;
    display: flex;
    flex-direction: column;
    gap: 1mm;
    align-items: center;
  }}
  .msg-savings  {{ font-size: 8.5pt; color: #166534; font-weight: 600; }}
  .msg-cashback {{ font-size: 8.5pt; color: #92400e; }}

  /* ── Notes ── */
  .note-block {{
    margin-top: 2.5mm;
    font-size: 8.5pt;
    color: #555;
    border-top: 0.25mm dotted #ccc;
    padding-top: 2.5mm;
  }}

  /* ── Footer ── */
  .footer {{
    margin-top: 5mm;
    border-top: 0.25mm dotted #ccc;
    padding-top: 2.5mm;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    font-size: 8pt;
    color: #aaa;
  }}
  .footer .terms {{ line-height: 1.5; }}
  .footer .thank {{ font-size: 8.5pt; color: #888; font-style: italic; }}
</style>
</head>
<body>

<!-- Header -->
<div class="hdr">
  <div class="hdr-left">
    <div class="hdr-shop">
      <img src="{_LOGO_URL}" alt="{SHOP_NAME_EN}">
    </div>
  </div>
  <div class="hdr-inv">
    <div class="iw">{t('চালান', 'INVOICE')}</div>
    <div class="in"># {order.order_number}</div>
  </div>
</div>

<!-- Meta strip: date · payment · status -->
<div class="meta-strip">
  <span class="meta-cell"><span class="mc-lbl">{t('তারিখ:', 'Date:')} </span><span class="mc-val">{order_date}</span></span>
  <span class="meta-cell"><span class="mc-lbl">{t('পেমেন্ট:', 'Payment:')} </span><span class="mc-val">{payment_label}</span></span>
  <span class="meta-cell"><span class="mc-lbl">{t('স্ট্যাটাস:', 'Status:')} </span>{paid_stamp}</span>
</div>

<!-- Billing: From (left) | To (right) -->
<div class="billing">
  <div class="bill-col" style="text-align:left;">
    <div class="bc-lbl">{t('প্রেরক', 'From')}</div>
    <div class="bc-name">{SHOP_NAME_BN} <span style="font-size:9pt;font-weight:normal;color:#777;">/ {SHOP_NAME_EN}</span></div>
    <div class="bc-info">{SHOP_WEB} &nbsp;·&nbsp; {SHOP_PHONE}</div>
    <div class="bc-info">{SHOP_ADDRESS}</div>
  </div>
  <div class="bill-divider"></div>
  <div class="bill-col" style="text-align:right;">
    <div class="bc-lbl">{t('গ্রাহক', 'Bill To')}</div>
    <div class="bc-name">{name}</div>
    <div class="bc-info">{order.shipping_phone}</div>
    {f'<div class="bc-info">{addr}</div>' if addr else ''}
    {f'<div class="bc-info">{location}</div>' if location else ''}
  </div>
</div>

<!-- Items -->
<table class="items-table">
  <thead>
    <tr>
      <th class="tc" style="width:8mm">#</th>
      <th style="text-align:left;">{t('পণ্য', 'Description')}</th>
      <th class="tc" style="width:14mm">{t('পরিমাণ', 'Qty')}</th>
      <th class="tr" style="width:28mm">{t('একক মূল্য', 'Unit Price')}</th>
      <th class="tr" style="width:28mm">{t('মোট', 'Amount')}</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<!-- Totals -->
<div class="totals-wrap">
  <div class="totals-qr">
    <img src="{qr_uri}" alt="QR">
    <p>pujarighar.com</p>
  </div>
  <table class="totals-table">
    <tbody>
      {totals_html}
    </tbody>
  </table>
</div>

{messages_html}
{notes_html}

<!-- Footer -->
<div class="footer">
  <div class="terms">
    {t('পণ্য গ্রহণের ৪৮ ঘণ্টার মধ্যে যেকোনো সমস্যা জানান।', 'Please report any issue within 48 hours of receiving your order.')}
  </div>
  <div class="thank">
    {t('কেনার জন্য ধন্যবাদ!', 'Thank you for your purchase!')}
  </div>
</div>

</body>
</html>"""


def _thermal_page_height(order: SalesOrder) -> str:
    mm = 85  # base: header + meta + separators + totals + footer
    for item in order.items.all():
        mm += 10  # item name line + qty×price line
        if item.product.is_package:
            mm += item.product.package_items.count() * 5
    return f'{mm + 10}mm'  # +10mm buffer


def _build_thermal_html(order: SalesOrder, lang: str, is_admin: bool = False) -> str:  # noqa: ARG001
    isBn = lang == 'bn'
    page_height = _thermal_page_height(order)
    def t(bn, en): return bn if isBn else en

    order_date = order.created_at.strftime('%b %d, %Y  %I:%M %p')
    name = (order.shipping_name_bn if isBn else order.shipping_name_en) or order.shipping_name_bn or order.shipping_name_en or ''

    items_html = ''
    for i, item in enumerate(order.items.all(), 1):
        item_name  = (item.product_name_bn if isBn else item.product_name_en) or item.product_name_bn
        qty        = int(float(item.quantity))
        unit_price = Decimal(str(item.unit_price))
        line_total = Decimal(str(item.line_total))
        orig_unit  = Decimal(str(item.original_unit_price)) if item.original_unit_price else unit_price
        is_pkg     = item.product.is_package

        unit_display = f'<s style="color:#bbb;font-size:6.5pt;">{_fmt(orig_unit)}</s> {_fmt(unit_price)}' if orig_unit > unit_price else _fmt(unit_price)

        items_html += f"""
<div class="item">
  <div class="item-name">{i}. {item_name}</div>
  <div class="item-row"><span>{qty} &times; ৳ {unit_display}</span><span>৳ {_fmt(line_total)}</span></div>
</div>"""

        if is_pkg:
            for pi in item.product.package_items.all():
                comp      = pi.component
                comp_name = (comp.name_bn if isBn else comp.name_en) or comp.name_bn
                comp_qty  = int(float(pi.quantity)) * qty
                items_html += f'<div class="sub-item">↳ {comp_name} &times; {comp_qty}</div>'

    discount_amount = Decimal(str(order.discount_amount or 0))
    payment_label   = t('ক্যাশ অন ডেলিভারি', 'Cash on Delivery') if order.payment_method == 'COD' else t('অনলাইন', 'Online')

    totals_html = f'<div class="tot-row"><span>{t("সাবটোটাল","Subtotal")}</span><span>৳ {_fmt(order.subtotal)}</span></div>'

    if discount_amount > 0:
        totals_html += f'<div class="tot-row disc"><span>{t("ছাড়","Discount")}</span><span>− ৳ {_fmt(discount_amount)}</span></div>'

    if Decimal(str(order.delivery_charge)) > 0:
        totals_html += f'<div class="tot-row"><span>{t("ডেলিভারি","Delivery")}</span><span>৳ {_fmt(order.delivery_charge)}</span></div>'

    if Decimal(str(order.tax_amount)) > 0:
        totals_html += f'<div class="tot-row"><span>{t("কর","Tax")}</span><span>৳ {_fmt(order.tax_amount)}</span></div>'

    paid_html = ''
    if order.payment_status == 'PAID':
        paid_html = f'<div class="tot-row"><span>{t("পরিশোধিত","Paid")}</span><span>৳ {_fmt(order.grand_total)}</span></div>'

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<style>
  @font-face {{
    font-family: 'NotoSansBengali';
    src: url('{_FONT_URL}') format('truetype');
  }}
  @page {{ size: 80mm {page_height}; margin: 0; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'NotoSansBengali', 'Courier New', monospace;
    font-size: 8pt;
    color: #000;
    background: #fff;
    padding: 4mm 5mm 6mm;
    width: 80mm;
  }}
  .center  {{ text-align: center; }}
  .shop-name {{ font-weight: bold; font-size: 10pt; margin-bottom: 0.5mm; }}
  .shop-info {{ font-size: 7.5pt; color: #444; line-height: 1.6; }}
  .sep {{ border: none; border-top: 1px dashed #000; margin: 2.5mm 0; }}
  .meta-row {{ display: flex; justify-content: space-between; font-size: 7.5pt; margin: 1mm 0; }}
  .meta-label {{ color: #555; }}
  .meta-val   {{ color: #000; text-align: right; }}
  .item {{ margin: 1.5mm 0; }}
  .item-name {{ font-weight: bold; font-size: 8pt; }}
  .item-row  {{ display: flex; justify-content: space-between; font-size: 7.5pt; color: #333; margin-top: 0.3mm; }}
  .sub-item  {{ font-size: 6.5pt; color: #999; padding-left: 3mm; margin-top: 0.3mm; }}
  .tot-row   {{ display: flex; justify-content: space-between; font-size: 7.5pt; margin: 0.8mm 0; }}
  .disc      {{ color: #166534; font-weight: 600; }}
  .grand     {{ font-weight: bold; font-size: 10pt; margin: 1.5mm 0 0.5mm; }}
  .thank     {{ font-size: 7.5pt; font-style: italic; color: #555; }}
</style>
</head>
<body>

<div class="center">
  <div class="shop-name">{SHOP_NAME_EN}</div>
  <div class="shop-info">{SHOP_ADDRESS}<br>{SHOP_PHONE}</div>
</div>

<hr class="sep">

<div class="meta-row"><span class="meta-label">{t("চালান","Invoice")}</span><span class="meta-val">{order.order_number}</span></div>
<div class="meta-row"><span class="meta-label">{t("তারিখ","Date")}</span><span class="meta-val">{order_date}</span></div>
<div class="meta-row"><span class="meta-label">{t("গ্রাহক","Customer")}</span><span class="meta-val">{name}</span></div>
<div class="meta-row"><span class="meta-label">{t("পেমেন্ট","Payment")}</span><span class="meta-val">{payment_label}</span></div>

<hr class="sep">

{items_html}

<hr class="sep">

{totals_html}
<div class="tot-row grand"><span>{t("সর্বমোট","Total")}</span><span>৳ {_fmt(order.grand_total)}</span></div>
{paid_html}

<hr class="sep">

<div class="center thank">{t("কেনার জন্য ধন্যবাদ!","Thank you for your purchase!")}</div>

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

    role     = request.user.role
    is_admin = role not in ('CUSTOMER',)

    # Customers can only download their own orders
    if role == 'CUSTOMER' and order.customer != request.user:
        return ApiResponse(message='Permission denied', errors='Forbidden', status_code=403)

    lang        = request.query_params.get('lang', 'en')
    disposition = request.query_params.get('disposition', 'inline')

    page_size = SiteSetting.get().invoice_page_size

    try:
        if page_size == 'THERMAL':
            html_str = _build_thermal_html(order, lang, is_admin=is_admin)
        else:
            html_str = _build_html(order, lang, is_admin=is_admin, page_size=page_size)
        font_config = FontConfiguration()
        pdf         = WeasyHTML(string=html_str).write_pdf(font_config=font_config)
    except Exception as e:
        logger.error(f'Invoice PDF generation failed: {e}', exc_info=True)
        return ApiResponse(message='PDF generation failed', errors=str(e), status_code=500)

    response = HttpResponse(pdf, content_type='application/pdf')
    filename = f'invoice-{order.order_number}.pdf'
    response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    return response
