SELECT s_store_name, SUM(ss_net_profit)
FROM store_sales, date_dim, store,
  (SELECT ca_zip
   FROM (
     SELECT SUBSTR(ca_zip, 1, 5) ca_zip FROM customer_address
     WHERE SUBSTR(ca_zip, 1, 5) IN (
       '24128','76232','65084','87816','83926','77556',
       '20548','26231','43848','15126','91137','61265',
       '98294','25782','17920','18103','98235','40081',
       '84093','28577','55565','17183','54601','67897',
       '22752','86284','18376','38607','45200','21756',
       '29741','96765','29461','10291','73693','69457',
       '17879','18191','70706','70072','35709','55259',
       '63121','13702','56881','40903','36742','11945',
       '82178','69399','20971','54364','54001','10567',
       '55125','61980','10090','71567','56241','84710',
       '49777','71781','44818','27834','70659'
     )
     INTERSECT
     SELECT ca_zip FROM (
       SELECT SUBSTR(ca_zip, 1, 5) ca_zip, COUNT(*) cnt
       FROM customer_address, customer
       WHERE ca_address_sk = c_current_addr_sk
         AND c_preferred_cust_flag = 'Y'
       GROUP BY ca_zip
       HAVING COUNT(*) > 10
     ) A1
   ) A2
  ) V1
WHERE ss_store_sk = s_store_sk
  AND ss_sold_date_sk = d_date_sk
  AND d_qoy = 2 AND d_year = 1998
  AND (SUBSTR(s_zip, 1, 2) = SUBSTR(V1.ca_zip, 1, 2))
GROUP BY s_store_name
ORDER BY s_store_name
LIMIT 100
