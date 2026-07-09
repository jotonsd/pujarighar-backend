from .auth_views import register, login, logout, token_refresh, google_login, facebook_login, forgot_password, reset_password
from .user_views import (
    list_users, create_user, get_user, update_user, delete_user,
    activate_user, deactivate_user, change_role,
    get_me, update_me, change_password,
    list_delivery_persons, lookup_user_by_phone,
)
from .product_views import (
    list_products, create_product, get_product, update_product, delete_product,
    add_product_image, delete_product_image, popular_by_category, get_recommended_products,
)
from .category_views import (
    list_categories, create_category, get_category, update_category, delete_category,
)
from .brand_views import (
    list_brands, create_brand, get_brand, update_brand, delete_brand,
)
from .stock_views import (
    get_stock, adjust_stock, get_purchase_report, get_supplier_return_report,
    list_package_items, add_package_item, delete_package_item,
)
from .cart_views import (
    get_cart, add_to_cart, update_cart_item,
    clear_cart, checkout,
)
from .guest_views import guest_checkout
from .shipping_views import (
    list_shipping_addresses, create_shipping_address,
    update_shipping_address, delete_shipping_address,
    set_default_shipping_address,
    admin_list_user_addresses, admin_create_user_address, admin_update_user_address,
)
from .payment_views import payment_ipn, payment_success, payment_fail, payment_cancel
from .order_views import (
    list_orders, get_order, get_order_tracking, get_order_status_log,
    track_by_order_number,
    confirm_order, pack_order, assign_delivery, dispatch_order,
    deliver_order, return_order, cancel_order, pos_create_order,
    mark_cod_paid, update_shipping,
)
from .banner_views import list_banners, list_all_banners, create_banner, update_banner, delete_banner
from .hero_slide_views import list_hero_slides, list_all_hero_slides, create_hero_slide, update_hero_slide, delete_hero_slide
from .accounting_views import (
    list_accounts, get_account,
    list_journal_entries, get_journal_entry,
    get_ledger,
    get_trial_balance, get_profit_loss, get_sales_summary,
    get_income_report, get_expense_report,
    get_dashboard_summary, create_manual_journal,
)
from .notification_views import list_notifications, list_all_notifications, mark_all_read, mark_one_read
from .discount_views import list_discounts, create_discount, toggle_discount, update_discount, delete_discount
from .review_views import (
    create_review, list_product_reviews, my_order_reviews,
    eligible_order_for_product,
    list_pending_reviews, approve_review, delete_review,
)
from .invoice_views import download_invoice
from .delivery_charge_views import get_delivery_charges, update_delivery_charges
from .cashback_views import list_cashback_tiers, create_cashback_tier, update_cashback_tier, delete_cashback_tier
from .supplier_views import (
    list_suppliers, create_supplier, get_supplier, update_supplier, delete_supplier,
    list_supplier_payments, create_supplier_payment, delete_supplier_payment,
)
from .partner_views import list_partners, create_partner, update_partner, delete_partner, list_partner_payments, create_partner_payment, update_partner_payment, delete_partner_payment
from .loan_views import (
    list_loan_investors, create_loan_investor, update_loan_investor, delete_loan_investor,
    list_loan_payments, create_loan_payment, delete_loan_payment,
)
from .settings_views import get_site_settings, update_site_settings
from .promo_email_views import list_promo_emails, promo_email_audience, create_promo_email, resend_promo_email
from .log_views import list_log_files, get_log_file
