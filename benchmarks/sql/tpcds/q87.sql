SELECT COUNT(*)
FROM (
  SELECT DISTINCT c_last_name, c_first_name, d_date
  FROM store_sales, date_dim, customer
  WHERE store_sales.ss_sold_date_sk = date_dim.d_date_sk
    AND store_sales.ss_customer_sk = customer.c_customer_sk
    AND d_month_seq BETWEEN 1200 AND 1200 + 11
) cool_cust
WHERE NOT EXISTS (
  SELECT * FROM (
    SELECT DISTINCT c_last_name, c_first_name, d_date
    FROM catalog_sales, date_dim, customer
    WHERE catalog_sales.cs_sold_date_sk = date_dim.d_date_sk
      AND catalog_sales.cs_bill_customer_sk = customer.c_customer_sk
      AND d_month_seq BETWEEN 1200 AND 1200 + 11
  ) cs_cust
  WHERE cool_cust.c_last_name = cs_cust.c_last_name
    AND cool_cust.c_first_name = cs_cust.c_first_name
    AND cool_cust.d_date = cs_cust.d_date
)
AND NOT EXISTS (
  SELECT * FROM (
    SELECT DISTINCT c_last_name, c_first_name, d_date
    FROM web_sales, date_dim, customer
    WHERE web_sales.ws_sold_date_sk = date_dim.d_date_sk
      AND web_sales.ws_bill_customer_sk = customer.c_customer_sk
      AND d_month_seq BETWEEN 1200 AND 1200 + 11
  ) ws_cust
  WHERE cool_cust.c_last_name = ws_cust.c_last_name
    AND cool_cust.c_first_name = ws_cust.c_first_name
    AND cool_cust.d_date = ws_cust.d_date
)
