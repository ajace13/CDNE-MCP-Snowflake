#### How many people rented Horror movies last summer?

- **Routed to:** `aggregate(metric='distinct_customers', start_date='2005-06-01', end_date='2005-08-31', group_by='none', category='Horror')`
- **Outcome:** answer
- **Headline shown:** 433 different customers rented Horror films, 2005-06-01 to 2005-08-31. Together they made 781 rentals.
- **Rows back:** 1; one row is: one row = the whole period
- **Cost:** 0.55 s elapsed, 351.0 KB scanned, ~0.000070 warehouse credits (estimate) + 0.000046 cloud-services credits
- **Check (ok):** You asked about customers, so it counted DISTINCT customer_id (781 rental rows would have been the wrong number). customer_id, not customer_key, because DIM_CUSTOMER keeps one row per version of a customer.
- **Check (info):** Assumption: Read 'summer' as summer 2005, the one the data covers.

```sql
SELECT COUNT(DISTINCT c.customer_id) AS value,
       COUNT(*) AS fact_rows,
       COUNT(DISTINCT c.customer_id) AS distinct_customers
FROM SAKILA.DW.FACT_RENTAL x
JOIN SAKILA.DW.DIM_FILM f     ON f.film_key = x.film_key
JOIN SAKILA.DW.DIM_STORE s    ON s.store_key = x.store_key
JOIN SAKILA.DW.DIM_CUSTOMER c ON c.customer_key = x.customer_key
WHERE x.rental_ts >= '2005-06-01'
  AND x.rental_ts < DATEADD(day, 1, '2005-08-31'::DATE)
  AND f.category = 'Horror'
```

Customers who rented Rentals matched Different customers
                 433             781                 433


#### Which film was rented the most?

- **Routed to:** no tool (cannot)
- **Outcome:** cannot
- **Headline shown:** These tools can't answer that question.
- **Warning shown:** These tools can't answer that: there is no data on that breakdown. The tools group only by month, category, store, country or rating; there is no per-customer, per-film or per-staff ranking. Rather than guess, no number is shown.
- **Check (ok):** Nothing was sent to Snowflake.


#### Revenue for Action and Comedy

- **Routed to:** `aggregate(metric='revenue', start_date='2005-05-24', end_date='2006-02-14', group_by='category')`
- **Outcome:** answer
- **Headline shown:** Revenue ($) by film category for all films, 2005-05-24 to 2006-02-14: Sports is highest ($5,314.21), Music lowest ($3,417.72), across 16 groups.
- **Rows back:** 16; one row is: one row = one film category
- **Cost:** 0.25 s elapsed, 290.5 KB scanned, ~0.000031 warehouse credits (estimate) + 0.000021 cloud-services credits
- **Check (info):** Assumption: Only one category can be filtered at a time, so this shows every category. Compare Action and Comedy in the table.

```sql
SELECT f.category AS group_value,
       SUM(x.amount) AS value,
       COUNT(*) AS fact_rows,
       COUNT(DISTINCT c.customer_id) AS distinct_customers
FROM SAKILA.DW.FACT_PAYMENT x
JOIN SAKILA.DW.DIM_FILM f     ON f.film_key = x.film_key
JOIN SAKILA.DW.DIM_STORE s    ON s.store_key = x.store_key
JOIN SAKILA.DW.DIM_CUSTOMER c ON c.customer_key = x.customer_key
WHERE x.payment_ts >= '2005-05-24'
  AND x.payment_ts < DATEADD(day, 1, '2006-02-14'::DATE)
GROUP BY 1
ORDER BY 2 DESC NULLS LAST, 1
LIMIT 200
```


#### Compare 2005 and 2006 revenue

- **Routed to:** `aggregate(metric='revenue', start_date='2005-01-01', end_date='2006-12-31', group_by='month')`
- **Outcome:** answer
- **Headline shown:** Revenue ($) by month for all films, 2005-01-01 to 2006-12-31: highest in 2005-07 ($28,368.91), lowest in 2006-02 ($514.18).
- **Warning shown:** There is no data at all for 2005-09 to 2006-01. The chart jumps over those months: that is a gap in the data, not a drop to zero.
- **Rows back:** 5; one row is: one row = one month
- **Cost:** 0.22 s elapsed, 290.5 KB scanned, ~0.000017 warehouse credits (estimate) + 0.000024 cloud-services credits
- **Check (info):** May 2005 (data starts on the 24th) and February 2006 (one day of data) are partial months, so they look low: 2005-05, 2006-02.
- **Check (info):** Note: payment data only runs from 2005-05-24 to 2006-02-14; the part of 2005-01-01 to 2006-12-31 outside that has no data.
- **Check (info):** Assumption: More than one year was named (2005, 2006), so this shows each month to compare them.

```sql
SELECT TO_CHAR(DATE_TRUNC('month', x.payment_ts), 'YYYY-MM') AS group_value,
       SUM(x.amount) AS value,
       COUNT(*) AS fact_rows,
       COUNT(DISTINCT c.customer_id) AS distinct_customers
FROM SAKILA.DW.FACT_PAYMENT x
JOIN SAKILA.DW.DIM_FILM f     ON f.film_key = x.film_key
JOIN SAKILA.DW.DIM_STORE s    ON s.store_key = x.store_key
JOIN SAKILA.DW.DIM_CUSTOMER c ON c.customer_key = x.customer_key
WHERE x.payment_ts >= '2005-01-01'
  AND x.payment_ts < DATEADD(day, 1, '2006-12-31'::DATE)
GROUP BY 1
ORDER BY 1
LIMIT 200
```

  Month Revenue ($) Payments Different customers
2005-05   $4,823.44    1,156                 520
2005-06   $9,629.89    2,311                 590
2005-07  $28,368.91    6,709                 599
2005-08  $24,070.14    5,686                 599
2006-02     $514.18      182                 158


#### How many rows are in fact_rental?

- **Routed to:** `describe_schema()`
- **Outcome:** answer
- **Headline shown:** The warehouse has 7 tables in SAKILA.DW. Rentals 2005-05-24 to 2006-02-14; payments 2005-05-24 to 2006-02-14. Questions about other dates have no data.
- **Rows back:** 7; one row is: One row of this answer = one table in the warehouse.
- **Cost:** 1.98 s elapsed, 8.5 KB scanned, ~0.000181 warehouse credits (estimate) + 0.000260 cloud-services credits
- **Check (info):** Not in the data: Costs, profit, inventory purchase prices, reviews or ratings by customers, web traffic, customer demographics (age, gender, income), anything after the last date above.

```sql
SELECT t.TABLE_NAME,
       t.TABLE_TYPE,
       t.ROW_COUNT,
       LISTAGG(c.COLUMN_NAME || ' ' || c.DATA_TYPE, ', ')
         WITHIN GROUP (ORDER BY c.ORDINAL_POSITION) AS COLUMNS
FROM SAKILA.INFORMATION_SCHEMA.TABLES t
JOIN SAKILA.INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME
WHERE t.TABLE_SCHEMA = 'DW'
GROUP BY t.TABLE_NAME, t.TABLE_TYPE, t.ROW_COUNT
ORDER BY t.TABLE_NAME
```

       Table      Kind                                                                                                                                               One row is  Rows                                                                                                                                                                                                                                                                                                                                  Columns
DIM_CUSTOMER dimension                          One row = one VERSION of a customer (Type 2). Count customers with COUNT(DISTINCT customer_id), never COUNT(*) or customer_key.   599 CUSTOMER_KEY NUMBER, CUSTOMER_ID NUMBER, FIRST_NAME TEXT, LAST_NAME TEXT, EMAIL TEXT, ACTIVE BOOLEAN, STORE_ID NUMBER, CREATE_DATE TIMESTAMP_NTZ, ADDRESS TEXT, ADDRESS2 TEXT, DISTRICT TEXT, POSTAL_CODE TEXT, PHONE TEXT, CITY TEXT, COUNTRY TEXT, ROW_HASH TEXT, VALID_FROM TIMESTAMP_NTZ, VALID_TO TIMESTAMP_NTZ, IS_CURRENT BOOLEAN
    DIM_DATE dimension                                                                         One row = one calendar day, 2005-01-01 to 2030-12-31, plus -1 for 'no date yet'.  9497                                                                                                                                                                  DATE_KEY NUMBER, FULL_DATE DATE, YEAR NUMBER, QUARTER NUMBER, MONTH NUMBER, MONTH_NAME TEXT, DAY_OF_MONTH NUMBER, DAY_OF_WEEK NUMBER, DAY_NAME TEXT, IS_WEEKEND BOOLEAN
    DIM_FILM dimension                                                                 One row = one VERSION of a film (Type 2), with its category and rental rate at the time.  1000      FILM_KEY NUMBER, FILM_ID NUMBER, TITLE TEXT, RELEASE_YEAR NUMBER, LANGUAGE TEXT, ORIGINAL_LANGUAGE TEXT, CATEGORY TEXT, RATING TEXT, SPECIAL_FEATURES TEXT, LENGTH NUMBER, RENTAL_DURATION NUMBER, RENTAL_RATE NUMBER, REPLACEMENT_COST NUMBER, ROW_HASH TEXT, VALID_FROM TIMESTAMP_NTZ, VALID_TO TIMESTAMP_NTZ, IS_CURRENT BOOLEAN
   DIM_STAFF dimension                                                                                                                     One row = one staff member (Type 1).     2                                                                                                                                                                                                       STAFF_KEY NUMBER, STAFF_ID NUMBER, STAFF_NAME TEXT, EMAIL TEXT, USERNAME TEXT, HOME_STORE_ID NUMBER, ACTIVE BOOLEAN, ROW_HASH TEXT
   DIM_STORE dimension                                                                                                      One row = one store (Type 1). There are two stores.     2                                                                                                                                                                                              STORE_KEY NUMBER, STORE_ID NUMBER, MANAGER_NAME TEXT, ADDRESS TEXT, DISTRICT TEXT, POSTAL_CODE TEXT, CITY TEXT, COUNTRY TEXT, ROW_HASH TEXT
FACT_PAYMENT      fact                       One row = one payment: one amount paid by one customer to one staff member on one date, against one rental. SUM(amount) = revenue. 16044                                                                                                                                                          PAYMENT_ID NUMBER, RENTAL_ID NUMBER, PAYMENT_DATE_KEY NUMBER, CUSTOMER_KEY NUMBER, FILM_KEY NUMBER, STORE_KEY NUMBER, STAFF_KEY NUMBER, PAYMENT_TS TIMESTAMP_NTZ, AMOUNT NUMBER
 FACT_RENTAL      fact One row = one rental: one copy of one film rented by one customer on one date, handled by one staff member. Counting rows counts rentals, NOT customers. 16044                                                                                  RENTAL_ID NUMBER, INVENTORY_ID NUMBER, RENTAL_DATE_KEY NUMBER, RETURN_DATE_KEY NUMBER, CUSTOMER_KEY NUMBER, FILM_KEY NUMBER, STORE_KEY NUMBER, STAFF_KEY NUMBER, RENTAL_TS TIMESTAMP_NTZ, RETURN_TS TIMESTAMP_NTZ, RENTAL_COUNT NUMBER, DAYS_OUT NUMBER


