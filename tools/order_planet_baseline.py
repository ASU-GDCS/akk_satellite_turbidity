# -*- coding: utf-8 -*-
#
# Author: Marcel König 
#
# This code enables integration of Planet imagery into Google Earth Engine.
#
# I tried to follow the setup guide at https://developers.planet.com/docs/integrations/gee/quickstart/#connect-to-google-earth-engine 
# but never got a response after sending the 'EE Account Upgrade Form' - this website is obviously outdated!
#
# The original code in this script has been written by Manthan Satish, a former grad student in Hannah Kerner's lab, and is available in his 
# github repo at https://github.com/kerner-lab/slice/tree/main/data/tools. Manthan was a great help in setting up the Google account to 
# enable integration of the Planet API and he recommended to order data in relatively small chunks of ~ 1-3 months at a time, depending on the filters applied. 
# I changed to code so it will accept some arguments when called from the command line.
#
# Later I found that Planet actually has a lot of helpful code examples hidden in their github repos at:
#
#         https://github.com/planetlabs/notebooks/blob/master/jupyter-notebooks/data-api-tutorials/search_and_download_quickstart.ipynb
#         https://github.com/planetlabs/notebooks/blob/master/jupyter-notebooks/data-api-tutorials/planet_python_client_introduction.ipynb
#         https://github.com/planetlabs/notebooks/blob/master/jupyter-notebooks/gee-integration/gee-integration.ipynb
#         https://github.com/planetlabs/notebooks/blob/master/jupyter-notebooks/orders_api_tutorials/Planet_SDK_Orders_demo.ipynb
#
#        
# Running the script requires the planet python package to be installed [!pip install planet]. Here's an example how to run the script from command line, just replace
# all capitalized arguments:
#
# PLANET_API_KEY=... python tools/order_planet_baseline.py -geojson_file YOUR_ROI.geojson -begin_datetime_string 2022-12-01T00:00:00.000Z -end_datetime_string 2022-12-31T23:59:59.000Z -cloud_cover 80.0 -order_name YOUR_ORDER_NAME -collection_name YOUR_COLLECTION_NAME -print_image_ids True -product_bundle analytic_8b_sr_udm2 -project_name YOUR_PROJECT_NAME -harmonize True
#
#
# !!! Before running the script you need to make sure that your Google account is prepared accordingly:
#
# 0. IMPORTANT: Use your private Google account. Your ASU account does not support the required writing access of the Planet service.
#
# 1. CREATE A GOOGLE CLOUD PROJECT IF NOT EXIST:
# 1.1 Go to console.cloud.google.com and create a new project or choose an existing project you want to work in. This guide seems to be pretty 
#     helpful and includes step 2: https://developers.google.com/earth-engine/cloud/earthengine_cloud_project_setup
#
# 2. CONFIGURE THE EARTH ENGINE API FOR THE PROJECT:
# 2.1 Go to console.cloud.google.com/apis and click the '+ ENABLE APIS AND SERVICES' 
# 2.2 Search for "Earth Engine" and enable API
#
# 3. GRANT PLANET WRITING ACCESS TO THE EARTH ENGINE:
# 3.1 Go to console.cloud.google.com/iam-admin 
# 3.2 Click 'GRANT ACCESS' and paste "planet-gee-uploader@planet-earthengine-staging.iam.gserviceaccount.com" into the field 'New principals'
# 3.3 Click the drop down button next to 'Select a role', go to 'Earth Engine' and select 'Earth Engine Resource Writer' 
#     This step will not work in your ASU Google account!
#
# 3. ACTIVATE THE PROJECT IN GEE:
# 3.1 Open the GEE Editor and click 'Assets' on the top left, then click 'ADD A PROJECT' and select your project from the dropdown menu. 
#     You will need the exact project name as displayed under 'CLOUD ASSETS' to run the script (argument: -project_name)
# 3.2 Click 'NEW' in the top left corner and then 'Image collection'. This is the collection that will host the data you are about to order through the Planet API. 
#     You will need the exact name of the ImageCollection as displayed under 'CLOUD ASSETS' to run the script (argument: -collection_name)

from pprint import pprint
import planet
import asyncio
import json
import requests
from requests.auth import HTTPBasicAuth
import argparse
import os
from datetime import datetime, timedelta, timezone


def get_image_ids(geojson_geometry, PLANET_API_KEY, begin_datetime_string, end_datetime_string, cloud_cover=0.2, item_type="PSScene", use_asset_filter=False, asset_filter="ortho_analytic_4b_sr"):
    """
    Get image IDs from Planet API using filters (region of interest, time period, cloud cover).
    Original code by Manthan Satish, avilable at: https://github.com/kerner-lab/slice/blob/main/data/tools/get_image_ids.py,
    slightly altered by Marcel Koenig. 

    A list of item_properties that can be used to filter images is found on the website for each item. E.g., for PSScenes at: https://developers.planet.com/docs/data/psscene/
    
    Args:
        geojson_geometry (_type_): GeoJSON geometry.
        PLANET_API_KEY (str): Your Planet API key available at https://www.planet.com/account/#/user-settings. 
        begin_datetime_string (str): Begin datetime of date range, format: '2023-09-01T00:00:00.000Z', days in UTC
        end_datetime_string (str): End datetime of date range, format: '2023-09-01T00:00:00.000Z', days in UTC
        cloud_cover (float, optional): Cloud cover threshold between 0 and 1. Defaults to 0.2.
        item_type (str): Represents the class of spacecraft and/or processing level. Defaults to "PSScene". A list of all items and assets is available at https://developers.planet.com/docs/apis/data/items-assets/.
        udm2 (bool): Flag to indicate if data needs to have Usable Data Mask. Defaults to True.

        
    Returns:
        List of image IDs
    """
    # get images that overlap with our AOI 
    geometry_filter = {
      "type": "GeometryFilter",
      "field_name": "geometry",
      "config": geojson_geometry
    }

    # get images acquired within a date range
    date_range_filter = {
      "type": "DateRangeFilter",
      "field_name": "acquired",
      "config": {
        "gte": begin_datetime_string,
        "lte": end_datetime_string
      }
    }

    # only get images which have <X% cloud coverage
    cloud_cover_filter = {
      "type": "RangeFilter",
      "field_name": "cloud_cover",
      "config": {
        "lte": cloud_cover
      }
    }

    # Fiter for assests. This was necessary as some items do not have all assets (see https://support.planet.com/hc/en-us/articles/4405971334685-How-to-Update-API-Searches-for-PSScene)
    # MAKE SURE THIS IS WORKING RIGHT!!
    asset_filter = {
    "type": "AssetFilter",
    "config": [
        asset_filter
        ]
    }

    # combine our filters
    combined_filter = {
    "type": "AndFilter",
    "config": [geometry_filter, date_range_filter, cloud_cover_filter, asset_filter]
    }

    item_type = item_type

    # API request object
    search_request = {
      "item_types": [item_type], 
      "filter": combined_filter
    }

    # fire off the POST request
    search_result = \
      requests.post(
        'https://api.planet.com/data/v1/quick-search',
        auth=HTTPBasicAuth(PLANET_API_KEY, ''),
        json=search_request)

    search_result = search_result.json()

    # extract image IDs only
    image_ids = [feature['id'] for feature in search_result['features']]
    
    return image_ids

# Create and deliver the order
async def create_and_deliver_order(auth, order_dict):
    '''Create and deliver an order.
    Parameters:
        order_request: An order request dict
        client: An Order client object
    '''
    async with planet.Session(auth=auth) as ps:

        client = ps.client('orders')

        with planet.reporting.StateBar(state='creating') as reporter:
            # Place an order to the Orders API
            order = await client.create_order(order_dict)
            reporter.update(state='created', order_id=order['id'])
            # Wait while the order is being completed
            await client.wait(order['id'],
                            callback=reporter.update_state,
                            max_attempts=0)
        # Grab the details of the orders
        order_details = await client.get_order(order_id=order['id'])
    
    return order_details
"""
async def exec_order(auth, image_order):
    async with planet.Session(auth=auth) as ps:
        # The Orders API client
        client = ps.client('orders')
        # Create the order and deliver it to GEE
        order_details = await create_and_deliver_order(image_order, client)
        # print(await client.get_order(order_id))

        return order_details
"""

async def push2gee(order_name=None, 
                  geojson_file=None, 
                  PLANET_API_KEY=None, 
                  t_start=None, 
                  t_stop=None, 
                  cloud_cover=None, 
                  item_type=None, 
                  use_asset_filter=None, 
                  asset_filter=None, 
                  print_image_ids=None, 
                  project_name=None, 
                  collection_name=None, 
                  product_bundle=None,
                  clip_with_geojson=None, 
                  harmonize=None):
    
    auth = planet.Auth.from_key(PLANET_API_KEY)

    # Test of order name has been provided and use datetime string if not
    if order_name is not None:
        order_name = order_name
    else:
        order_name = datetime.now().strftime('%Y%m%d%H%M%S')
    print('Order name: '+order_name)
    
    # Test if input_file exists
    if not os.path.exists(geojson_file):
        raise RuntimeError(f"Could not find file {geojson_file}")
    else:
        with open(geojson_file) as aoi:
            aoi = aoi.read()
            aoi_geom = json.loads(aoi)["features"][0]["geometry"]

    # Get image IDs according to filter args
    image_ids = get_image_ids(geojson_geometry=aoi_geom, 
                              PLANET_API_KEY=PLANET_API_KEY, 
                              begin_datetime_string=t_start,
                              end_datetime_string=t_stop,
                              cloud_cover=cloud_cover,
                              item_type=item_type,
                              use_asset_filter=use_asset_filter,
                              asset_filter=asset_filter)

    # Print image IDs if specified
    if print_image_ids:
        print(image_ids)

    # Google Earth Engine configuration: Planet delivers straight into the EE collection.
    # (An earlier version delivered to GCS with an embedded service-account key; never do that.)
    cloud_config = planet.order_request.google_earth_engine(
        project=project_name, collection=collection_name)

    # Order delivery configuration
    delivery_config = planet.order_request.delivery(cloud_config=cloud_config)

    # Product description for the order request
    data_products = [
        planet.order_request.product(item_ids=image_ids,
                                     product_bundle=product_bundle,
                                     item_type=item_type)
    ]

    # Clip images to the AOI's perimeter and/or harmonize the data with Sentinel-2
    tools = []

    bandmath = {
    "bandmath": {
      "pixel_type": "16s", # "32R",
      "b1": "b1",
      "b2": "b2",
      "b3": "b4",
      "b4": "b6",
      "b5": "b8"
      }
    }

    # tools are executed by Planet in a particular order, regardless of order in this list
    if harmonize:
        tools.append(planet.order_request.harmonize_tool('Sentinel-2'))
    if clip_with_geojson:
        tools.append(planet.order_request.clip_tool(aoi_geom))
    tools.append(bandmath)

    # Build the order request
    image_order = planet.order_request.build_request(name=order_name, products=data_products, delivery=delivery_config, tools=tools)
    try:
        order_details = await create_and_deliver_order(auth, image_order)
    except Exception:
        print(Exception)

    pprint(order_details)
    
    return order_details


def main():
    """
    The following lines are original code by Manthan Satish, avilable at: 
    https://github.com/kerner-lab/slice/blob/main/data/tools/order_planet_images.py, and were slightly altered by Marcel Koenig, 
    so the script can be run from command line with arguments.
    Actually, the code's original source is this notebook: https://github.com/planetlabs/notebooks/blob/master/jupyter-notebooks/gee-integration/gee-integration.ipynb
    """

    parser=argparse.ArgumentParser(description="Order Planet imagery and insert into ImageCollection in GoogleEarthEngine")
    parser.add_argument('-planet_api_key_file', type=str, default=None, help="File containing your Planet API key. Defaults to the PLANET_API_KEY environment variable.")
    parser.add_argument('-geojson_file', type=str, help="Path to GeoJSON file defining the region of interest")
    parser.add_argument('-begin_datetime_string', type=str, help="Begin datetime of date range, format: '2023-09-01T00:00:00.000Z', days in UTC")
    parser.add_argument('-end_datetime_string', type=str, help="End datetime of date range, format: '2023-09-01T00:00:00.000Z', days in UTC")
    parser.add_argument('-cloud_cover', type=float, default=0.2, help="Cloud cover threshold between 0 and 1. Defaults to 0.2")
    parser.add_argument('-order_name', type=str, help="Name of your order to show up on https://www.planet.com/account/#/orders. Will be datetime if not defined.")
    parser.add_argument('-project_name', type=str, help="Name of the GCP project showing in 'CLOUD ASSETS' on GEE")
    parser.add_argument('-collection_name', type=str, help="Name of the ImageCollection the ordered images will be inserted to. MUST BE EMPTY!")
    parser.add_argument('-print_image_ids', type=bool, default=True, help="Print image IDs? [True/False]. Default: True.")
    parser.add_argument('-item_type', type=str, default="PSScene", help="Represents the class of spacecraft and/or processing level. A list of all items and assets is available at https://developers.planet.com/docs/apis/data/items-assets/.")
    parser.add_argument('-product_bundle', type=str, default="analytic_8b_sr_udm2", help="analytic|analytic_udm2|analytic_3b_udm2|analytic_5b|analytic_5b_udm2|analytic_8b_udm2|visual|uncalibrated_dn|uncalibrated_dn_udm2|basic_analytic|basic_analytic_udm2|basic_analytic_8b_udm2|basic_uncalibrated_dn|basic_uncalibrated_dn_udm2|analytic_sr|analytic_sr_udm2|analytic_8b_sr_udm2|basic_analytic_nitf|basic_panchromatic|basic_panchromatic_dn|panchromatic|panchromatic_dn|panchromatic_dn_udm2|pansharpened|pansharpened_udm2|basic_l1a_dn [https://planet-sdk-for-python-v2.readthedocs.io/en/latest/cli/cli-reference/?h=bundle]")
    parser.add_argument('-harmonize', type=bool, default=False, help="Radiometrically armonize data with Sentinel-2? [True/False]. Default: False.")
    parser.add_argument('-clip_with_geojson', type=bool, default=False, help="Clip imagery using the provided GeoJSON geometry? [True/False]. Default: False.")
    parser.add_argument('-use_asset_filter', type=bool, default=False, help="Apply AssetFilter for get_image_ids()? [True/False]. Default: False.")
    parser.add_argument('-asset_filter', type=str, default="ortho_analytic_4b_sr", help="AssetFilter argument for get_image_ids() . Default: 'ortho_analytic_4b_sr'.")
    
    args = parser.parse_args()

    if args.planet_api_key_file:
        with open(args.planet_api_key_file) as api_key_file:
            planet_api_key = api_key_file.read().replace('\n', '')
    else:
        planet_api_key = os.environ.get("PLANET_API_KEY", "").strip()
    if not planet_api_key:
        raise SystemExit("Set PLANET_API_KEY or pass -planet_api_key_file")


    delta = timedelta(days=1)

    start_date = args.begin_datetime_string
    YYYY, MM, DD = start_date.split('-')
    start_date = datetime(int(YYYY), int(MM), int(DD), 0, 0, 0, tzinfo=timezone.utc)
    
    end_date = args.end_datetime_string
    YYYY, MM, DD = end_date.split('-')
    end_date = datetime(int(YYYY), int(MM), int(DD), 0, 0, 0, tzinfo=timezone.utc)

    while start_date <= end_date:
        query_begin = f'{start_date.year:02d}-{start_date.month:02d}-{start_date.day:02d}T00:00:00.000Z'
        start_date += 10*delta
        query_end = f'{start_date.year:02d}-{start_date.month:02d}-{start_date.day:02d}T23:59:59.000Z'

        print(query_begin)
        print(query_end)
        
        order_details = asyncio.run(push2gee(args.order_name, args.geojson_file, planet_api_key, 
                      query_begin,
                      query_end, 
                      args.cloud_cover, args.item_type, 
                      args.use_asset_filter, args.asset_filter, args.print_image_ids,
                      args.project_name, args.collection_name, args.product_bundle,
                      args.clip_with_geojson, args.harmonize))
        
        print(order_details)
        print()

if __name__ == "__main__":
    main()
