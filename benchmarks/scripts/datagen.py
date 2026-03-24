#!/usr/bin/env python3
"""Generate TPC-DS data in Parquet format using dsdgen binary."""

import argparse
import os
import subprocess
import sys
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, LongType,
    DecimalType, DateType,
)

# TPC-DS table schemas: (table_name, column_definitions)
# Column definitions: list of (name, type) tuples
# Types: s=string, i=int, l=long, d=decimal(7,2), D=decimal(15,2), dt=date
TABLES = {
    "call_center": "cc_call_center_sk:i,cc_call_center_id:s,cc_rec_start_date:dt,cc_rec_end_date:dt,cc_closed_date_sk:i,cc_open_date_sk:i,cc_name:s,cc_class:s,cc_employees:i,cc_sq_ft:i,cc_hours:s,cc_manager:s,cc_mkt_id:i,cc_mkt_class:s,cc_mkt_desc:s,cc_market_manager:s,cc_division:i,cc_division_name:s,cc_company:i,cc_company_name:s,cc_street_number:s,cc_street_name:s,cc_street_type:s,cc_suite_number:s,cc_city:s,cc_county:s,cc_state:s,cc_zip:s,cc_country:s,cc_gmt_offset:d,cc_tax_percentage:d",
    "catalog_page": "cp_catalog_page_sk:i,cp_catalog_page_id:s,cp_start_date_sk:i,cp_end_date_sk:i,cp_department:s,cp_catalog_number:i,cp_catalog_page_number:i,cp_description:s,cp_type:s",
    "catalog_returns": "cr_returned_date_sk:i,cr_returned_time_sk:i,cr_item_sk:i,cr_refunded_customer_sk:i,cr_refunded_cdemo_sk:i,cr_refunded_hdemo_sk:i,cr_refunded_addr_sk:i,cr_returning_customer_sk:i,cr_returning_cdemo_sk:i,cr_returning_hdemo_sk:i,cr_returning_addr_sk:i,cr_call_center_sk:i,cr_catalog_page_sk:i,cr_ship_mode_sk:i,cr_warehouse_sk:i,cr_reason_sk:i,cr_order_number:l,cr_return_quantity:i,cr_return_amount:D,cr_return_tax:D,cr_return_amt_inc_tax:D,cr_fee:D,cr_return_ship_cost:D,cr_refunded_cash:D,cr_reversed_charge:D,cr_store_credit:D,cr_net_loss:D",
    "catalog_sales": "cs_sold_date_sk:i,cs_sold_time_sk:i,cs_ship_date_sk:i,cs_bill_customer_sk:i,cs_bill_cdemo_sk:i,cs_bill_hdemo_sk:i,cs_bill_addr_sk:i,cs_ship_customer_sk:i,cs_ship_cdemo_sk:i,cs_ship_hdemo_sk:i,cs_ship_addr_sk:i,cs_call_center_sk:i,cs_catalog_page_sk:i,cs_ship_mode_sk:i,cs_warehouse_sk:i,cs_item_sk:i,cs_promo_sk:i,cs_order_number:l,cs_quantity:i,cs_wholesale_cost:D,cs_list_price:D,cs_sales_price:D,cs_ext_discount_amt:D,cs_ext_sales_price:D,cs_ext_wholesale_cost:D,cs_ext_list_price:D,cs_ext_tax:D,cs_coupon_amt:D,cs_ext_ship_cost:D,cs_net_paid:D,cs_net_paid_inc_tax:D,cs_net_paid_inc_ship:D,cs_net_paid_inc_ship_tax:D,cs_net_profit:D",
    "customer": "c_customer_sk:i,c_customer_id:s,c_current_cdemo_sk:i,c_current_hdemo_sk:i,c_current_addr_sk:i,c_first_shipto_date_sk:i,c_first_sales_date_sk:i,c_salutation:s,c_first_name:s,c_last_name:s,c_preferred_cust_flag:s,c_birth_day:i,c_birth_month:i,c_birth_year:i,c_birth_country:s,c_login:s,c_email_address:s,c_last_review_date:s",
    "customer_address": "ca_address_sk:i,ca_address_id:s,ca_street_number:s,ca_street_name:s,ca_street_type:s,ca_suite_number:s,ca_city:s,ca_county:s,ca_state:s,ca_zip:s,ca_country:s,ca_gmt_offset:d,ca_location_type:s",
    "customer_demographics": "cd_demo_sk:i,cd_gender:s,cd_marital_status:s,cd_education_status:s,cd_purchase_estimate:i,cd_credit_rating:s,cd_dep_count:i,cd_dep_employed_count:i,cd_dep_college_count:i",
    "date_dim": "d_date_sk:i,d_date_id:s,d_date:dt,d_month_seq:i,d_week_seq:i,d_quarter_seq:i,d_year:i,d_dow:i,d_moy:i,d_dom:i,d_qoy:i,d_fy_year:i,d_fy_quarter_seq:i,d_fy_week_seq:i,d_day_name:s,d_quarter_name:s,d_holiday:s,d_weekend:s,d_following_holiday:s,d_first_dom:i,d_last_dom:i,d_same_day_ly:i,d_same_day_lq:i,d_current_day:s,d_current_week:s,d_current_month:s,d_current_quarter:s,d_current_year:s",
    "dbgen_version": "dv_version:s,dv_create_date:dt,dv_create_time:s,dv_cmdline_args:s",
    "household_demographics": "hd_demo_sk:i,hd_income_band_sk:i,hd_buy_potential:s,hd_dep_count:i,hd_vehicle_count:i",
    "income_band": "ib_income_band_sk:i,ib_lower_bound:i,ib_upper_bound:i",
    "inventory": "inv_date_sk:i,inv_item_sk:i,inv_warehouse_sk:i,inv_quantity_on_hand:i",
    "item": "i_item_sk:i,i_item_id:s,i_rec_start_date:dt,i_rec_end_date:dt,i_item_desc:s,i_current_price:D,i_wholesale_cost:D,i_brand_id:i,i_brand:s,i_class_id:i,i_class:s,i_category_id:i,i_category:s,i_manufact_id:i,i_manufact:s,i_size:s,i_formulation:s,i_color:s,i_units:s,i_container:s,i_manager_id:i,i_product_name:s",
    "promotion": "p_promo_sk:i,p_promo_id:s,p_start_date_sk:i,p_end_date_sk:i,p_item_sk:i,p_cost:D,p_response_target:i,p_promo_name:s,p_channel_dmail:s,p_channel_email:s,p_channel_catalog:s,p_channel_tv:s,p_channel_radio:s,p_channel_press:s,p_channel_event:s,p_channel_demo:s,p_channel_details:s,p_purpose:s,p_discount_active:s",
    "reason": "r_reason_sk:i,r_reason_id:s,r_reason_desc:s",
    "ship_mode": "sm_ship_mode_sk:i,sm_ship_mode_id:s,sm_type:s,sm_code:s,sm_carrier:s,sm_contract:s",
    "store": "s_store_sk:i,s_store_id:s,s_rec_start_date:dt,s_rec_end_date:dt,s_closed_date_sk:i,s_store_name:s,s_number_employees:i,s_floor_space:i,s_hours:s,s_manager:s,s_market_id:i,s_geography_class:s,s_market_desc:s,s_market_manager:s,s_division_id:i,s_division_name:s,s_company_id:i,s_company_name:s,s_street_number:s,s_street_name:s,s_street_type:s,s_suite_number:s,s_city:s,s_county:s,s_state:s,s_zip:s,s_country:s,s_gmt_offset:d,s_tax_precentage:d",
    "store_returns": "sr_returned_date_sk:i,sr_return_time_sk:i,sr_item_sk:i,sr_customer_sk:i,sr_cdemo_sk:i,sr_hdemo_sk:i,sr_addr_sk:i,sr_store_sk:i,sr_reason_sk:i,sr_ticket_number:l,sr_return_quantity:i,sr_return_amt:D,sr_return_tax:D,sr_return_amt_inc_tax:D,sr_fee:D,sr_return_ship_cost:D,sr_refunded_cash:D,sr_reversed_charge:D,sr_store_credit:D,sr_net_loss:D",
    "store_sales": "ss_sold_date_sk:i,ss_sold_time_sk:i,ss_item_sk:i,ss_customer_sk:i,ss_cdemo_sk:i,ss_hdemo_sk:i,ss_addr_sk:i,ss_store_sk:i,ss_promo_sk:i,ss_ticket_number:l,ss_quantity:i,ss_wholesale_cost:D,ss_list_price:D,ss_sales_price:D,ss_ext_discount_amt:D,ss_ext_sales_price:D,ss_ext_wholesale_cost:D,ss_ext_list_price:D,ss_ext_tax:D,ss_coupon_amt:D,ss_net_paid:D,ss_net_paid_inc_tax:D,ss_net_profit:D",
    "time_dim": "t_time_sk:i,t_time_id:s,t_time:i,t_hour:i,t_minute:i,t_second:i,t_am_pm:s,t_shift:s,t_sub_shift:s,t_meal_time:s",
    "warehouse": "w_warehouse_sk:i,w_warehouse_id:s,w_warehouse_name:s,w_warehouse_sq_ft:i,w_street_number:s,w_street_name:s,w_street_type:s,w_suite_number:s,w_city:s,w_county:s,w_state:s,w_zip:s,w_country:s,w_gmt_offset:d",
    "web_page": "wp_web_page_sk:i,wp_web_page_id:s,wp_rec_start_date:dt,wp_rec_end_date:dt,wp_creation_date_sk:i,wp_access_date_sk:i,wp_autogen_flag:s,wp_customer_sk:i,wp_url:s,wp_type:s,wp_char_count:i,wp_link_count:i,wp_image_count:i,wp_max_ad_count:i",
    "web_returns": "wr_returned_date_sk:i,wr_returned_time_sk:i,wr_item_sk:i,wr_refunded_customer_sk:i,wr_refunded_cdemo_sk:i,wr_refunded_hdemo_sk:i,wr_refunded_addr_sk:i,wr_returning_customer_sk:i,wr_returning_cdemo_sk:i,wr_returning_hdemo_sk:i,wr_returning_addr_sk:i,wr_web_page_sk:i,wr_reason_sk:i,wr_order_number:l,wr_return_quantity:i,wr_return_amt:D,wr_return_tax:D,wr_return_amt_inc_tax:D,wr_fee:D,wr_return_ship_cost:D,wr_refunded_cash:D,wr_reversed_charge:D,wr_account_credit:D,wr_net_loss:D",
    "web_sales": "ws_sold_date_sk:i,ws_sold_time_sk:i,ws_ship_date_sk:i,ws_item_sk:i,ws_bill_customer_sk:i,ws_bill_cdemo_sk:i,ws_bill_hdemo_sk:i,ws_bill_addr_sk:i,ws_ship_customer_sk:i,ws_ship_cdemo_sk:i,ws_ship_hdemo_sk:i,ws_ship_addr_sk:i,ws_web_page_sk:i,ws_web_site_sk:i,ws_ship_mode_sk:i,ws_warehouse_sk:i,ws_promo_sk:i,ws_order_number:l,ws_quantity:i,ws_wholesale_cost:D,ws_list_price:D,ws_sales_price:D,ws_ext_discount_amt:D,ws_ext_sales_price:D,ws_ext_wholesale_cost:D,ws_ext_list_price:D,ws_ext_tax:D,ws_coupon_amt:D,ws_ext_ship_cost:D,ws_net_paid:D,ws_net_paid_inc_tax:D,ws_net_paid_inc_ship:D,ws_net_paid_inc_ship_tax:D,ws_net_profit:D",
    "web_site": "web_site_sk:i,web_site_id:s,web_rec_start_date:dt,web_rec_end_date:dt,web_name:s,web_open_date_sk:i,web_close_date_sk:i,web_class:s,web_manager:s,web_mkt_id:i,web_mkt_class:s,web_mkt_desc:s,web_market_manager:s,web_company_id:i,web_company_name:s,web_street_number:s,web_street_name:s,web_street_type:s,web_suite_number:s,web_city:s,web_county:s,web_state:s,web_zip:s,web_country:s,web_gmt_offset:d,web_tax_percentage:d",
}

TYPE_MAP = {
    "s": StringType(),
    "i": IntegerType(),
    "l": LongType(),
    "d": DecimalType(7, 2),
    "D": DecimalType(15, 2),
    "dt": DateType(),
}


def parse_schema(spec: str) -> StructType:
    """Parse compact schema spec into StructType."""
    fields = []
    for col_spec in spec.split(","):
        name, type_code = col_spec.split(":")
        fields.append(StructField(name.strip(), TYPE_MAP[type_code.strip()], True))
    return StructType(fields)


# Child tables are generated as a side effect of their parent table.
# dbgen_version is generated as a side effect of any dsdgen run.
CHILD_TABLES = {
    "catalog_returns",   # generated by catalog_sales
    "store_returns",     # generated by store_sales
    "web_returns",       # generated by web_sales
    "dbgen_version",     # generated as side effect
}


def run_dsdgen(table: str, scale_factor: int, output_dir: str, dsdgen_dir: str, force: bool = False):
    """Run dsdgen for a single table (skip child tables and already-generated)."""
    if table in CHILD_TABLES:
        print(f"Skipping dsdgen for {table} (child table, generated by parent)")
        return
    dat_file = os.path.join(output_dir, f"{table}.dat")
    if not force and os.path.exists(dat_file) and os.path.getsize(dat_file) > 0:
        print(f"Skipping dsdgen for {table}: {dat_file} already exists")
        return
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        os.path.join(dsdgen_dir, "dsdgen"),
        "-TABLE", table,
        "-SCALE", str(scale_factor),
        "-DIR", output_dir,
        "-SUFFIX", ".dat",
        "-DELIMITER", "|",
        "-TERMINATE", "N",
        "-FORCE", "Y",
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=dsdgen_dir)


def parquet_exists(parquet_dir: str, table: str) -> bool:
    """Check if Parquet data already exists for a table."""
    table_dir = os.path.join(parquet_dir, table)
    if not os.path.isdir(table_dir):
        return False
    # Check for at least one .parquet file
    for f in os.listdir(table_dir):
        if f.endswith(".parquet"):
            return True
    return False


def load_table(spark, table: str, schema: StructType, dat_dir: str, parquet_dir: str, force: bool = False):
    """Load a pipe-delimited .dat file into Parquet."""
    if not force and parquet_exists(parquet_dir, table):
        print(f"Skipping {table}: Parquet already exists at {parquet_dir}/{table}")
        return
    dat_file = os.path.join(dat_dir, f"{table}.dat")
    if not os.path.exists(dat_file):
        print(f"Skipping {table}: {dat_file} not found")
        return
    out_path = os.path.join(parquet_dir, table)
    print(f"Converting {table} -> {out_path}")
    df = spark.read.csv(dat_file, schema=schema, sep="|", header=False)
    df.write.mode("overwrite").parquet(out_path)
    print(f"  {table}: {df.count()} rows written")


def main():
    parser = argparse.ArgumentParser(description="Generate TPC-DS data")
    parser.add_argument("--scale-factor", type=int, default=1, help="TPC-DS scale factor (default: 1)")
    parser.add_argument("--data-dir", default="/data/tpcds", help="Base data directory")
    parser.add_argument("--dsdgen-dir", default="/opt/tpcds-kit/tools", help="dsdgen binary directory")
    parser.add_argument("--force-datagen", action="store_true", help="Force regeneration even if data exists")
    args = parser.parse_args()

    dat_dir = os.path.join(args.data_dir, "raw")
    parquet_dir = os.path.join(args.data_dir, "parquet")
    os.makedirs(dat_dir, exist_ok=True)
    os.makedirs(parquet_dir, exist_ok=True)

    spark = SparkSession.builder \
        .appName("TPC-DS Data Generation") \
        .getOrCreate()

    # Generate raw data with dsdgen
    for table in TABLES:
        run_dsdgen(table, args.scale_factor, dat_dir, args.dsdgen_dir, args.force_datagen)

    # Convert to Parquet
    for table, schema_spec in TABLES.items():
        schema = parse_schema(schema_spec)
        load_table(spark, table, schema, dat_dir, parquet_dir, args.force_datagen)

    spark.stop()
    print("Data generation complete.")


if __name__ == "__main__":
    main()
