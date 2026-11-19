-- CI360 Schema: Setup Foreign Key Relationships
-- Safe to re-run: each constraint is added only if it does not already exist.
-- Uses plain prepared statements (no stored procedures), so no CREATE ROUTINE
-- privilege is needed.

USE manyikt_Customer_Intelligence;

-- ============================================
-- 1. Drop churn_predictions table (no longer needed)
--    Churn scores live in hm_customers.churn_risk_score
--    (written by score_churn.py, read by the API).
-- ============================================
DROP TABLE IF EXISTS churn_predictions;

-- ============================================
-- 2. Orphan checks -- every count below must be 0.
--    A foreign key cannot be added while orphans exist; if any count is
--    non-zero, fix or delete those rows first.
--    (The hm_transactions checks scan ~31M rows and may take a while.)
-- ============================================
SELECT 'hm_transactions -> hm_customers' AS relationship, COUNT(*) AS orphan_rows
FROM hm_transactions t LEFT JOIN hm_customers c ON c.customer_id = t.customer_id
WHERE c.customer_id IS NULL
UNION ALL
SELECT 'hm_transactions -> hm_articles', COUNT(*)
FROM hm_transactions t LEFT JOIN hm_articles a ON a.article_id = t.article_id
WHERE a.article_id IS NULL
UNION ALL
SELECT 'article_summary -> hm_articles', COUNT(*)
FROM article_summary s LEFT JOIN hm_articles a ON a.article_id = s.article_id
WHERE a.article_id IS NULL;

-- ============================================
-- 3. Add Foreign Keys (only if missing)
--
-- hm_transactions uses ON DELETE RESTRICT: deleting a customer or article
-- must not silently erase sales history. article_summary is derived data,
-- so CASCADE is acceptable there. (Segments live in crm_customers, built by
-- build_crm_customers.py; there is no separate customer_segments table.)
--
-- Note: if an older run of this script already created fk_transactions_*
-- with ON DELETE CASCADE, it is dropped and re-created with RESTRICT.
-- ============================================

-- Drop legacy CASCADE versions of the transaction FKs, if present
SET @sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE hm_transactions DROP FOREIGN KEY fk_transactions_customer',
        'SELECT ''fk_transactions_customer: no legacy CASCADE constraint'' AS info')
    FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND CONSTRAINT_NAME = 'fk_transactions_customer'
      AND DELETE_RULE = 'CASCADE'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (
    SELECT IF(COUNT(*) > 0,
        'ALTER TABLE hm_transactions DROP FOREIGN KEY fk_transactions_article',
        'SELECT ''fk_transactions_article: no legacy CASCADE constraint'' AS info')
    FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND CONSTRAINT_NAME = 'fk_transactions_article'
      AND DELETE_RULE = 'CASCADE'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- hm_transactions.customer_id -> hm_customers.customer_id
SET @sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE hm_transactions ADD CONSTRAINT fk_transactions_customer
            FOREIGN KEY (customer_id) REFERENCES hm_customers(customer_id)
            ON DELETE RESTRICT ON UPDATE CASCADE',
        'SELECT ''fk_transactions_customer already exists'' AS info')
    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'hm_transactions'
      AND CONSTRAINT_NAME = 'fk_transactions_customer'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- hm_transactions.article_id -> hm_articles.article_id
SET @sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE hm_transactions ADD CONSTRAINT fk_transactions_article
            FOREIGN KEY (article_id) REFERENCES hm_articles(article_id)
            ON DELETE RESTRICT ON UPDATE CASCADE',
        'SELECT ''fk_transactions_article already exists'' AS info')
    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'hm_transactions'
      AND CONSTRAINT_NAME = 'fk_transactions_article'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- article_summary.article_id -> hm_articles.article_id
SET @sql = (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE article_summary ADD CONSTRAINT fk_article_summary_article
            FOREIGN KEY (article_id) REFERENCES hm_articles(article_id)
            ON DELETE CASCADE ON UPDATE CASCADE',
        'SELECT ''fk_article_summary_article already exists'' AS info')
    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'article_summary'
      AND CONSTRAINT_NAME = 'fk_article_summary_article'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ============================================
-- 4. Verify Relationships
-- ============================================
SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME,
       k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, r.DELETE_RULE
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS r
  ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME
WHERE k.TABLE_SCHEMA = 'manyikt_Customer_Intelligence'
  AND k.CONSTRAINT_NAME LIKE 'fk_%'
ORDER BY k.TABLE_NAME;

-- ============================================
-- 5. Verify Table Structure
-- ============================================
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'manyikt_Customer_Intelligence'
AND TABLE_NAME IN ('hm_customers', 'hm_articles', 'hm_transactions', 'article_summary')
ORDER BY TABLE_NAME, ORDINAL_POSITION;
