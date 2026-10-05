from django.db import migrations

# Fixes a migration-history/schema desync: an earlier deploy ran 0115 under
# an older single-field version (ProductImage.color_label, CartItem.color,
# SalesOrderItem.color) before it was changed to bilingual color_bn/color_en
# fields with the same migration name — so a later deploy's `migrate` saw
# "0115 already applied" and skipped it, leaving the old single-field schema
# in place on any environment that had already run the old version.
#
# This migration only touches the database (no state_operations) since
# Django's model state already matches the current models.py from 0115 on
# every environment — only the ACTUAL columns are out of sync on whichever
# environment ran the old 0115. Entirely idempotent: a no-op wherever
# color_bn/color_en already exist (e.g. local dev, or any environment that
# never ran the old version).
FORWARD_SQL = r"""
DO $$
DECLARE
    old_name text;
    exists_new boolean;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_productimage' AND column_name='color_label')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_productimage' AND column_name='color_bn') THEN
        ALTER TABLE api_productimage RENAME COLUMN color_label TO color_bn;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_productimage' AND column_name='color_bn') THEN
        ALTER TABLE api_productimage ADD COLUMN color_bn VARCHAR(40) NOT NULL DEFAULT '';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_productimage' AND column_name='color_en') THEN
        ALTER TABLE api_productimage ADD COLUMN color_en VARCHAR(40) NOT NULL DEFAULT '';
        UPDATE api_productimage SET color_en = color_bn WHERE color_bn != '';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_cartitem' AND column_name='color')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_cartitem' AND column_name='color_bn') THEN
        ALTER TABLE api_cartitem RENAME COLUMN color TO color_bn;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_cartitem' AND column_name='color_bn') THEN
        ALTER TABLE api_cartitem ADD COLUMN color_bn VARCHAR(40) NOT NULL DEFAULT '';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_cartitem' AND column_name='color_en') THEN
        ALTER TABLE api_cartitem ADD COLUMN color_en VARCHAR(40) NOT NULL DEFAULT '';
        UPDATE api_cartitem SET color_en = color_bn WHERE color_bn != '';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_salesorderitem' AND column_name='color')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_salesorderitem' AND column_name='color_bn') THEN
        ALTER TABLE api_salesorderitem RENAME COLUMN color TO color_bn;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_salesorderitem' AND column_name='color_bn') THEN
        ALTER TABLE api_salesorderitem ADD COLUMN color_bn VARCHAR(40) NOT NULL DEFAULT '';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_salesorderitem' AND column_name='color_en') THEN
        ALTER TABLE api_salesorderitem ADD COLUMN color_en VARCHAR(40) NOT NULL DEFAULT '';
        UPDATE api_salesorderitem SET color_en = color_bn WHERE color_bn != '';
    END IF;

    -- CartItem's unique_together: old deploy created it over (cart,product,color);
    -- replace with the current (cart,product,color_bn,color_en) version if needed.
    SELECT tc.constraint_name INTO old_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    WHERE tc.table_name = 'api_cartitem' AND tc.constraint_type = 'UNIQUE'
    GROUP BY tc.constraint_name
    HAVING array_agg(kcu.column_name::text ORDER BY kcu.column_name::text) = array['cart_id','color_bn','product_id'];

    IF old_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE api_cartitem DROP CONSTRAINT %I', old_name);
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.table_name = 'api_cartitem' AND tc.constraint_type = 'UNIQUE'
        GROUP BY tc.constraint_name
        HAVING array_agg(kcu.column_name::text ORDER BY kcu.column_name::text) = array['cart_id','color_bn','color_en','product_id']
    ) INTO exists_new;

    IF NOT exists_new THEN
        ALTER TABLE api_cartitem ADD CONSTRAINT api_cartitem_cart_product_color_uniq
            UNIQUE (cart_id, product_id, color_bn, color_en);
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0115_product_color_variants'),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
