import duckdb
import logging
import sys
import os
import argparse

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from config import DB_PATH, CSV_FILES

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

EXPECTED_TYPES = {
    "orders": {
        "order_id": "INTEGER",
        "user_id": "INTEGER",
        "eval_set": "VARCHAR",
        "order_number": "INTEGER",
        "order_dow": "INTEGER",
        "order_hour_of_day": "INTEGER",
        "days_since_prior_order": "DOUBLE",
    },
    "order_products_prior": {
        "order_id": "BIGINT",
        "product_id": "BIGINT",
        "add_to_cart_order": "BIGINT",
        "reordered": "BIGINT",
    },
    "order_products_train": {
        "order_id": "BIGINT",
        "product_id": "BIGINT",
        "add_to_cart_order": "BIGINT",
        "reordered": "BIGINT",
    },
    "products": {
        "product_id": "BIGINT",
        "aisle_id": "BIGINT",
        "department_id": "BIGINT",
    },
}


def _check_csv_files_exist():
    missing = [name for name, path in CSV_FILES.items() if not path.exists()]
    if missing:
        logger.error("Missing CSV files in data/raw/:")
        for name in missing:
            logger.error(f"  {name}.csv not found at {CSV_FILES[name]}")
        logger.error(
            "Download from: https://www.kaggle.com/datasets/psparks/instacart-market-basket-analysis"
            " and place all CSVs in data/raw/"
        )
        sys.exit(1)


def _check_already_ingested(con) -> bool:
    try:
        tables = con.execute("SHOW TABLES").fetchdf()["name"].tolist()
        return all(t in tables for t in CSV_FILES.keys())
    except Exception:
        return False


def _validate_types(con, table_name: str):
    if table_name not in EXPECTED_TYPES:
        return
    actual = con.execute(f"""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = '{table_name}'
    """).fetchdf()
    actual_map = dict(zip(actual["column_name"], actual["data_type"]))
    mismatches = [
        f"    {col}: expected {exp}, got {actual_map.get(col, 'MISSING')}"
        for col, exp in EXPECTED_TYPES[table_name].items()
        if actual_map.get(col) != exp
    ]
    if mismatches:
        logger.warning(f"Type mismatches in {table_name}:\n" + "\n".join(mismatches))
    else:
        logger.info(f"  {table_name}: types OK")


def _validate_data_quality(con):
    logger.info("Running data quality checks ...")

    checks = [
        (
            "reordered flag values (prior)",
            "SELECT COUNT(*) FROM order_products_prior WHERE reordered NOT IN (0, 1)",
            0,
        ),
        (
            "order_dow range 0-6",
            "SELECT COUNT(*) FROM orders WHERE order_dow NOT BETWEEN 0 AND 6",
            0,
        ),
        (
            "order_hour_of_day range 0-23",
            "SELECT COUNT(*) FROM orders WHERE order_hour_of_day NOT BETWEEN 0 AND 23",
            0,
        ),
        (
            "no orphan product_ids in order_products_prior",
            "SELECT COUNT(DISTINCT op.product_id) FROM order_products_prior op LEFT JOIN products p ON op.product_id = p.product_id WHERE p.product_id IS NULL",
            0,
        ),
        (
            "no orphan order_ids in order_products_prior",
            "SELECT COUNT(DISTINCT op.order_id) FROM order_products_prior op LEFT JOIN orders o ON op.order_id = o.order_id WHERE o.order_id IS NULL",
            0,
        ),
        (
            "NULL days_since_prior_order only for order_number=1",
            "SELECT COUNT(*) FROM orders WHERE days_since_prior_order IS NULL AND order_number != 1",
            0,
        ),
        (
            "no 'missing' aisle or department in product_full",
            "SELECT COUNT(*) FROM product_full WHERE aisle = 'missing' OR department = 'missing'",
            0,
        ),
    ]

    all_passed = True
    for label, query, expected in checks:
        result = con.execute(query).fetchone()[0]
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            logger.info(f"  [{status}] {label}")
        else:
            logger.warning(f"  [{status}] {label} — got {result}, expected {expected}")
            all_passed = False

    return all_passed


def ingest(force: bool = False):
    _check_csv_files_exist()

    logger.info(f"Connecting to DuckDB at: {DB_PATH}")
    con = duckdb.connect(str(DB_PATH))

    if not force and _check_already_ingested(con):
        logger.info("Tables already exist. Skipping ingest. Use --force to re-ingest.")
        con.close()
        return

    for table_name, csv_path in CSV_FILES.items():
        logger.info(f"Loading {table_name} from {csv_path.name} ...")

        if table_name == "orders":
            con.execute(f"""
                CREATE OR REPLACE TABLE orders AS
                SELECT
                    CAST(order_id AS INTEGER)                                    AS order_id,
                    CAST(user_id AS INTEGER)                                     AS user_id,
                    eval_set,
                    CAST(order_number AS INTEGER)                                AS order_number,
                    CAST(order_dow AS INTEGER)                                   AS order_dow,
                    CAST(order_hour_of_day AS INTEGER)                           AS order_hour_of_day,
                    TRY_CAST(NULLIF(TRIM(days_since_prior_order), '') AS DOUBLE) AS days_since_prior_order
                FROM read_csv_auto('{csv_path}', all_varchar=true)
            """)
        else:
            con.execute(f"""
                CREATE OR REPLACE TABLE {table_name} AS
                SELECT * FROM read_csv_auto('{csv_path}', nullstr='NA')
            """)

        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        logger.info(f"  {table_name}: {count:,} rows loaded")
        _validate_types(con, table_name)

    logger.info("Creating view: order_products ...")
    con.execute("""
        CREATE OR REPLACE VIEW order_products AS
        SELECT order_id, product_id, add_to_cart_order, reordered, 'prior' AS eval_set
        FROM order_products_prior
        UNION ALL
        SELECT order_id, product_id, add_to_cart_order, reordered, 'train' AS eval_set
        FROM order_products_train
    """)
    count = con.execute("SELECT COUNT(*) FROM order_products").fetchone()[0]
    logger.info(f"  order_products (unioned): {count:,} rows")

    logger.info("Creating view: product_full ...")
    con.execute("""
        CREATE OR REPLACE VIEW product_full AS
        SELECT
            p.product_id,
            p.product_name,
            p.aisle_id,
            a.aisle,
            p.department_id,
            d.department
        FROM products p
        JOIN aisles a ON p.aisle_id = a.aisle_id
        JOIN departments d ON p.department_id = d.department_id
        WHERE a.aisle != 'missing'
        AND d.department != 'missing'
    """)
    count = con.execute("SELECT COUNT(*) FROM product_full").fetchone()[0]
    logger.info(f"  product_full: {count:,} rows (excluding 'missing' aisle/department)")

    all_passed = _validate_data_quality(con)
    if not all_passed:
        logger.warning("Some data quality checks failed — review before proceeding.")

    logger.info("3-table join smoke test:")
    sample = con.execute("""
        SELECT o.order_id, o.user_id, pf.product_name, pf.aisle, pf.department
        FROM orders o
        JOIN order_products op ON o.order_id = op.order_id
        JOIN product_full pf ON op.product_id = pf.product_id
        LIMIT 3
    """).fetchdf()
    logger.info("\n" + sample.to_string(index=False))

    con.close()
    logger.info("Ingest complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force re-ingest even if tables already exist")
    args = parser.parse_args()
    ingest(force=args.force)