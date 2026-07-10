from django.urls import path
from . import views

urlpatterns = [
    # ─── Auth ─────────────────────────────────────────────────────────────────
    path('auth/register/',       views.register,      name='auth-register'),
    path('auth/login/',          views.login,         name='auth-login'),
    path('auth/logout/',         views.logout,        name='auth-logout'),
    path('auth/token/refresh/',  views.token_refresh, name='auth-token-refresh'),
    path('auth/google/',          views.google_login,   name='auth-google'),
    path('auth/facebook/',        views.facebook_login, name='auth-facebook'),
    path('auth/forgot-password/', views.forgot_password, name='auth-forgot-password'),
    path('auth/reset-password/',  views.reset_password,  name='auth-reset-password'),

    # ─── Users ────────────────────────────────────────────────────────────────
    path('users/',                         views.list_users,           name='user-list'),
    path('users/me/',                      views.get_me,               name='user-me'),
    path('users/me/update/',               views.update_me,            name='user-me-update'),
    path('users/me/change-password/',      views.change_password,      name='user-change-password'),
    path('users/delivery-persons/',        views.list_delivery_persons,  name='delivery-persons'),
    path('users/lookup-by-phone/',         views.lookup_user_by_phone,   name='user-lookup-phone'),
    path('users/create/',                  views.create_user,          name='user-create'),
    path('users/<uuid:pk>/',               views.get_user,             name='user-detail'),
    path('users/<uuid:pk>/update/',        views.update_user,          name='user-update'),
    path('users/<uuid:pk>/delete/',        views.delete_user,          name='user-delete'),
    path('users/<uuid:pk>/activate/',      views.activate_user,        name='user-activate'),
    path('users/<uuid:pk>/deactivate/',    views.deactivate_user,      name='user-deactivate'),
    path('users/<uuid:pk>/change-role/',   views.change_role,          name='user-change-role'),

    # ─── Categories ───────────────────────────────────────────────────────────
    path('categories/',             views.list_categories,  name='category-list'),
    path('categories/create/',      views.create_category,  name='category-create'),
    path('categories/<uuid:pk>/',   views.get_category,     name='category-detail'),
    path('categories/<uuid:pk>/update/', views.update_category, name='category-update'),
    path('categories/<uuid:pk>/delete/', views.delete_category, name='category-delete'),

    # ─── Brands ───────────────────────────────────────────────────────────────
    path('brands/',                      views.list_brands,   name='brand-list'),
    path('brands/create/',               views.create_brand,  name='brand-create'),
    path('brands/<uuid:pk>/',            views.get_brand,     name='brand-detail'),
    path('brands/<uuid:pk>/update/',     views.update_brand,  name='brand-update'),
    path('brands/<uuid:pk>/delete/',     views.delete_brand,  name='brand-delete'),

    # ─── Products ─────────────────────────────────────────────────────────────
    path('products/',                          views.list_products,         name='product-list'),
    path('products/create/',                   views.create_product,        name='product-create'),
    path('products/popular-by-category/',      views.popular_by_category,   name='popular-by-category'),
    path('products/recommended/',              views.get_recommended_products, name='product-recommended'),
    path('products/<uuid:pk>/',                views.get_product,           name='product-detail'),
    path('products/slug/<slug:slug>/',         views.get_product_by_slug,   name='product-detail-by-slug'),
    path('products/<uuid:pk>/update/', views.update_product, name='product-update'),
    path('products/<uuid:pk>/delete/', views.delete_product, name='product-delete'),
    path('products/<uuid:pk>/images/', views.add_product_image, name='product-image-add'),
    path('products/<uuid:pk>/images/<uuid:image_id>/', views.delete_product_image, name='product-image-delete'),

    # ─── Stock ────────────────────────────────────────────────────────────────
    path('products/<uuid:pk>/stock/',          views.get_stock,          name='product-stock'),
    path('products/<uuid:pk>/stock/adjust/',   views.adjust_stock,       name='stock-adjust'),
    path('reports/purchases/',                 views.get_purchase_report,        name='purchase-report'),
    path('reports/supplier-returns/',          views.get_supplier_return_report, name='supplier-return-report'),
    path('reports/income/',                    views.get_income_report,          name='income-report'),
    path('reports/expenses/',                  views.get_expense_report,         name='expense-report'),
    path('products/<uuid:pk>/package-items/',  views.list_package_items, name='package-items'),
    path('products/<uuid:pk>/package-items/add/', views.add_package_item, name='package-item-add'),
    path('products/<uuid:pk>/package-items/<uuid:item_id>/delete/', views.delete_package_item, name='package-item-delete'),

    # ─── Shipping Addresses ───────────────────────────────────────────────────
    path('shipping-addresses/',                        views.list_shipping_addresses,      name='shipping-address-list'),
    path('shipping-addresses/create/',                 views.create_shipping_address,      name='shipping-address-create'),
    path('shipping-addresses/<uuid:pk>/update/',       views.update_shipping_address,      name='shipping-address-update'),
    path('shipping-addresses/<uuid:pk>/delete/',       views.delete_shipping_address,      name='shipping-address-delete'),
    path('shipping-addresses/<uuid:pk>/set-default/',  views.set_default_shipping_address, name='shipping-address-set-default'),
    path('users/<uuid:user_id>/shipping-addresses/',                    views.admin_list_user_addresses,   name='admin-user-address-list'),
    path('users/<uuid:user_id>/shipping-addresses/create/',             views.admin_create_user_address,   name='admin-user-address-create'),
    path('users/<uuid:user_id>/shipping-addresses/<uuid:pk>/update/',   views.admin_update_user_address,   name='admin-user-address-update'),

    # ─── Cart ─────────────────────────────────────────────────────────────────
    path('cart/',                          views.get_cart,        name='cart'),
    path('cart/items/',                    views.add_to_cart,     name='cart-item-add'),
    path('cart/items/<uuid:item_id>/',     views.update_cart_item, name='cart-item-detail'),
    path('cart/clear/',                    views.clear_cart,      name='cart-clear'),
    path('cart/checkout/',                 views.checkout,        name='cart-checkout'),
    path('cart/guest-checkout/',           views.guest_checkout,  name='guest-checkout'),

    # ─── Payments (SSLCommerz callbacks) ─────────────────────────────────────
    path('payments/ipn/',     views.payment_ipn,     name='payment-ipn'),
    path('payments/success/', views.payment_success, name='payment-success'),
    path('payments/fail/',    views.payment_fail,    name='payment-fail'),
    path('payments/cancel/',  views.payment_cancel,  name='payment-cancel'),

    # ─── Orders ───────────────────────────────────────────────────────────────
    path('orders/track/',                          views.track_by_order_number, name='order-track-public'),
    path('orders/pos-create/',                   views.pos_create_order,   name='pos-create-order'),
    path('orders/',                              views.list_orders,        name='order-list'),
    path('orders/<uuid:pk>/',                    views.get_order,          name='order-detail'),
    path('orders/<uuid:pk>/tracking/',           views.get_order_tracking, name='order-tracking'),
    path('orders/<uuid:pk>/status-log/',         views.get_order_status_log, name='order-status-log'),
    path('orders/<uuid:pk>/confirm/',            views.confirm_order,      name='order-confirm'),
    path('orders/<uuid:pk>/pack/',               views.pack_order,         name='order-pack'),
    path('orders/<uuid:pk>/assign-delivery/',    views.assign_delivery,    name='order-assign-delivery'),
    path('orders/<uuid:pk>/dispatch/',           views.dispatch_order,     name='order-dispatch'),
    path('orders/<uuid:pk>/deliver/',            views.deliver_order,      name='order-deliver'),
    path('orders/<uuid:pk>/return/',             views.return_order,       name='order-return'),
    path('orders/<uuid:pk>/cancel/',             views.cancel_order,       name='order-cancel'),
    path('orders/<uuid:pk>/mark-cod-paid/',      views.mark_cod_paid,      name='order-mark-cod-paid'),
    path('orders/<uuid:pk>/update-shipping/',   views.update_shipping,    name='order-update-shipping'),
    path('orders/<uuid:pk>/invoice/',            views.download_invoice,   name='order-invoice'),

    # ─── Suppliers ────────────────────────────────────────────────────────────
    path('suppliers/',                                              views.list_suppliers,           name='supplier-list'),
    path('suppliers/create/',                                       views.create_supplier,          name='supplier-create'),
    path('suppliers/<uuid:pk>/',                                    views.get_supplier,             name='supplier-detail'),
    path('suppliers/<uuid:pk>/update/',                             views.update_supplier,          name='supplier-update'),
    path('suppliers/<uuid:pk>/delete/',                             views.delete_supplier,          name='supplier-delete'),
    path('suppliers/<uuid:pk>/payments/',                           views.list_supplier_payments,   name='supplier-payment-list'),
    path('suppliers/<uuid:pk>/payments/create/',                    views.create_supplier_payment,  name='supplier-payment-create'),
    path('suppliers/<uuid:pk>/payments/<uuid:payment_pk>/delete/',  views.delete_supplier_payment,  name='supplier-payment-delete'),

    # ─── Partners ─────────────────────────────────────────────────────────────
    path('partners/',                                                       views.list_partners,           name='partner-list'),
    path('partners/create/',                                                views.create_partner,          name='partner-create'),
    path('partners/<uuid:pk>/update/',                                      views.update_partner,          name='partner-update'),
    path('partners/<uuid:pk>/delete/',                                      views.delete_partner,          name='partner-delete'),
    path('partners/<uuid:pk>/payments/',                                    views.list_partner_payments,   name='partner-payment-list'),
    path('partners/<uuid:pk>/payments/create/',                             views.create_partner_payment,  name='partner-payment-create'),
    path('partners/<uuid:pk>/payments/<uuid:payment_pk>/update/',           views.update_partner_payment,  name='partner-payment-update'),
    path('partners/<uuid:pk>/payments/<uuid:payment_pk>/delete/',           views.delete_partner_payment,  name='partner-payment-delete'),

    # ─── Loan Investors ───────────────────────────────────────────────────────
    path('loans/',                                                          views.list_loan_investors,     name='loan-list'),
    path('loans/create/',                                                   views.create_loan_investor,    name='loan-create'),
    path('loans/<uuid:pk>/update/',                                         views.update_loan_investor,    name='loan-update'),
    path('loans/<uuid:pk>/delete/',                                         views.delete_loan_investor,    name='loan-delete'),
    path('loans/<uuid:pk>/payments/',                                       views.list_loan_payments,      name='loan-payment-list'),
    path('loans/<uuid:pk>/payments/create/',                                views.create_loan_payment,     name='loan-payment-create'),
    path('loans/<uuid:pk>/payments/<uuid:payment_pk>/delete/',              views.delete_loan_payment,     name='loan-payment-delete'),

    # ─── Accounting ───────────────────────────────────────────────────────────
    path('accounting/accounts/',                      views.list_accounts,         name='account-list'),
    path('accounting/accounts/<uuid:pk>/',             views.get_account,           name='account-detail'),
    path('accounting/journal-entries/',                views.list_journal_entries,  name='journal-list'),
    path('accounting/journal-entries/create/',         views.create_manual_journal, name='journal-create'),
    path('accounting/journal-entries/<uuid:pk>/',      views.get_journal_entry,     name='journal-detail'),
    path('accounting/ledger/<uuid:account_id>/',       views.get_ledger,            name='ledger'),
    path('accounting/reports/trial-balance/',          views.get_trial_balance,     name='trial-balance'),
    path('accounting/reports/profit-loss/',            views.get_profit_loss,       name='profit-loss'),
    path('accounting/reports/sales-summary/',          views.get_sales_summary,     name='sales-summary'),

    # ─── Dashboard ────────────────────────────────────────────────────────────
    path('dashboard/summary/', views.get_dashboard_summary, name='dashboard-summary'),

    # ─── Banners ──────────────────────────────────────────────────────────────
    path('banners/',                  views.list_banners,     name='banner-list'),
    path('banners/all/',              views.list_all_banners, name='banner-list-all'),
    path('banners/create/',           views.create_banner,   name='banner-create'),
    path('banners/<uuid:pk>/update/', views.update_banner,   name='banner-update'),
    path('banners/<uuid:pk>/delete/', views.delete_banner,   name='banner-delete'),

    # ─── Hero Slides ──────────────────────────────────────────────────────────
    path('hero-slides/',                  views.list_hero_slides,     name='hero-slide-list'),
    path('hero-slides/all/',              views.list_all_hero_slides, name='hero-slide-list-all'),
    path('hero-slides/create/',           views.create_hero_slide,    name='hero-slide-create'),
    path('hero-slides/<uuid:pk>/update/', views.update_hero_slide,    name='hero-slide-update'),
    path('hero-slides/<uuid:pk>/delete/', views.delete_hero_slide,    name='hero-slide-delete'),

    # ─── Promo Emails ─────────────────────────────────────────────────────────
    path('promo-emails/',                views.list_promo_emails,    name='promo-email-list'),
    path('promo-emails/audience/',       views.promo_email_audience, name='promo-email-audience'),
    path('promo-emails/create/',         views.create_promo_email,   name='promo-email-create'),
    path('promo-emails/<uuid:pk>/resend/', views.resend_promo_email, name='promo-email-resend'),

    # ─── Delivery Charges ─────────────────────────────────────────────────────
    path('delivery-charges/',        views.get_delivery_charges,    name='delivery-charges-get'),
    path('delivery-charges/update/', views.update_delivery_charges, name='delivery-charges-update'),

    # ─── Cashback Tiers ───────────────────────────────────────────────────────
    path('cashback/',              views.list_cashback_tiers,   name='cashback-list'),
    path('cashback/create/',       views.create_cashback_tier,  name='cashback-create'),
    path('cashback/<int:pk>/update/', views.update_cashback_tier, name='cashback-update'),
    path('cashback/<int:pk>/delete/', views.delete_cashback_tier, name='cashback-delete'),

    # ─── Discounts ────────────────────────────────────────────────────────────
    path('discounts/',                      views.list_discounts,   name='discount-list'),
    path('discounts/create/',               views.create_discount,  name='discount-create'),
    path('discounts/<uuid:pk>/toggle/',     views.toggle_discount,  name='discount-toggle'),
    path('discounts/<uuid:pk>/update/',     views.update_discount,  name='discount-update'),
    path('discounts/<uuid:pk>/delete/',     views.delete_discount,  name='discount-delete'),

    # ─── Notifications ────────────────────────────────────────────────────────
    path('notifications/',                      views.list_notifications,     name='notification-list'),
    path('notifications/all/',                  views.list_all_notifications, name='notification-list-all'),
    path('notifications/mark-all-read/',        views.mark_all_read,      name='notification-mark-all'),
    path('notifications/<uuid:pk>/mark-read/',  views.mark_one_read,      name='notification-mark-one'),

    # ─── Reviews ──────────────────────────────────────────────────────────────
    path('reviews/',                            views.create_review,         name='review-create'),
    path('reviews/my-order/',                   views.my_order_reviews,      name='review-my-order'),
    path('reviews/pending/',                    views.list_pending_reviews,  name='review-pending'),
    path('reviews/<uuid:pk>/approve/',          views.approve_review,        name='review-approve'),
    path('reviews/<uuid:pk>/delete/',           views.delete_review,         name='review-delete'),
    path('products/<uuid:pk>/reviews/',         views.list_product_reviews,       name='product-reviews'),
    path('products/<uuid:pk>/eligible-order/', views.eligible_order_for_product, name='product-eligible-order'),

    # ─── Site Settings ─────────────────────────────────────────────────────────
    path('settings/',        views.get_site_settings,    name='site-settings'),
    path('settings/update/', views.update_site_settings, name='site-settings-update'),

    # ─── Logs ──────────────────────────────────────────────────────────────────
    path('logs/',              views.list_log_files, name='log-list'),
    path('logs/<str:filename>/', views.get_log_file,  name='log-detail'),
]
