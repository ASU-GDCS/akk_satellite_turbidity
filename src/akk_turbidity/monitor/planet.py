import planet
import ee

from . import utils
from .turbidity_monitor import DayDeferred, TurbidityMonitor

import asyncio
from datetime import datetime, timezone
import json
import os
from pprint import pprint
import requests
from requests.auth import HTTPBasicAuth
import time

# Order states before a final one (success, partial, failed, cancelled)
ORDER_IN_FLIGHT = ('queued', 'running')

class PlanetTurbidity(TurbidityMonitor):
    # Planet->GEE delivery usually takes 15-25 min but can take an hour. The wait is capped
    # so the CircleCI job (60 min max) isn't killed mid-wait; an order still in flight is
    # left running and picked up by the next run instead of being ordered again.
    order_timeout_s = 2100
    order_poll_s = 60

    def __init__(self, 
                 credentials = None,
                 project: str = None,
                 assets_root: str = None,
                 date: str = None,
                 aoi_loc: str = None,
                 local_aoi_loc: str = None,
                 planet_baselines_loc: str = None,
                 planet_daily_loc: str = None,
                 bucket:str = None,
                 prefix: str = None,
                 planet_api_key: str = None,
                 dep_loc: str = None,
                 ):
        # set instance variables
        self.name = "planet"
        self.credentials = credentials
        self.project = project
        self.assets_root = assets_root
        self.aoi_loc = aoi_loc
        self.dep_loc = dep_loc
        self.date = date
        self.set_time_interval(date)
        self.bucket = bucket
        self.prefix = prefix
        self.planet_api_key = planet_api_key

        # must init GEE before setting remaining variables
        self.init_ee()

        self.aoi = ee.FeatureCollection(aoi_loc)
        self.local_aoi_loc = local_aoi_loc
        self.baselines = ee.Image(planet_baselines_loc)
        self.dep = ee.Image(dep_loc)
        self.daily = planet_daily_loc

        self.target = f"{self.assets_root}/{self.daily}/{self.date}"

    def collect_image_ids(self, geojson_geometry, PLANET_API_KEY, begin_datetime_string, end_datetime_string, cloud_cover=0.2, item_type="PSScene", use_asset_filter=False,  asset_filter="ortho_analytic_8b_sr"): # asset_filter="ortho_analytic_4b_sr"):
        geometry_filter = {
          "type": "GeometryFilter",
          "field_name": "geometry",
          "config": geojson_geometry
        }

        date_range_filter = {
          "type": "DateRangeFilter",
          "field_name": "acquired",
          "config": {
            "gte": begin_datetime_string,
            "lte": end_datetime_string
          }
        }

        cloud_cover_filter = {
          "type": "RangeFilter",
          "field_name": "cloud_cover",
          "config": {
            "lte": cloud_cover
          }
        }

        asset_filter = {
        "type": "AssetFilter",
        "config": [
            asset_filter
            ]
        }

        combined_filter = {
        "type": "AndFilter",
        "config": [geometry_filter, date_range_filter, cloud_cover_filter, asset_filter]
        }

        search_request = {
          "item_types": [item_type], 
          "filter": combined_filter
        }

        search_result = \
          requests.post(
            'https://api.planet.com/data/v1/quick-search',
            auth=HTTPBasicAuth(PLANET_API_KEY, ''),
            json=search_request)

        search_result.raise_for_status()
        search_result = search_result.json()

        image_ids = [feature['id'] for feature in search_result['features']]

        return image_ids

    async def order_and_push(self, 
                      order_name=None, 
                      geojson_file=None, 
                      PLANET_API_KEY=None, 
                      t_start=None, 
                      t_stop=None, 
                      cloud_cover=None, 
                      item_type=None, 
                      use_asset_filter=None, 
                      asset_filter=None,
                      project_name=None, 
                      collection_name=None, 
                      product_bundle=None,
                      clip_with_geojson=None, 
                      harmonize=None):

        auth = planet.Auth.from_key(PLANET_API_KEY)

        if order_name is not None:
            order_name = order_name
        else:
            order_name = self.date
        print(f'Order name: {order_name}')

        # A previous run may have placed this day's order and been stopped before Planet
        # delivered it. Wait on that order instead: ordering again would delete what it has
        # already delivered and pay for the same scenes twice.
        async with planet.Session(auth=auth) as ps:
            client = ps.client('orders')
            previous = await self.previous_order(client, order_name, collection_name)
            if previous is not None:
                print(f"Resuming order {previous['id']} ({previous['state']}, placed {previous['created_on']})")
                order_details = await self.wait_for_order(client, previous['id'])
                pprint(order_details)
                return order_details

        self.cleanup()

        if not os.path.exists(geojson_file):
            raise RuntimeError(f"Could not find file {geojson_file}")
        else:
            with open(geojson_file) as aoi:
                aoi = aoi.read()
                aoi_geom = json.loads(aoi)["features"][0]["geometry"]

        image_ids = self.collect_image_ids(geojson_geometry=aoi_geom, 
                                  PLANET_API_KEY=PLANET_API_KEY, 
                                  begin_datetime_string=t_start,
                                  end_datetime_string=t_stop,
                                  cloud_cover=cloud_cover,
                                  item_type=item_type,
                                  use_asset_filter=use_asset_filter,
                                  asset_filter=asset_filter)

        if len(image_ids) == 0:
            print(f"No Planet scenes found for {self.date}")
            return None

        cloud_config = planet.order_request.google_earth_engine(
            project=project_name, collection=collection_name)

        delivery_config = planet.order_request.delivery(cloud_config=cloud_config)

        data_products = [
            planet.order_request.product(item_ids=image_ids,
                                         product_bundle=product_bundle,
                                         item_type=item_type)
        ]

        tools = []

        bandmath = {
        "bandmath": {
          "pixel_type": "16S", #32R no need for scale factor when cpnvertng to int (just truncates, no linear scaling)
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

        image_order = planet.order_request.build_request(name=order_name, products=data_products, delivery=delivery_config, tools=tools)

        # order_details = await self.process_order(auth, image_order)

        collection_path = f"{self.assets_root}/{collection_name}"
        if ee.data.getInfo(collection_path) is not None:
            ee.data.deleteAsset(collection_path)
        ee.data.createAsset({'type': 'ImageCollection'}, path=collection_path)

        async  with planet.Session(auth=auth) as ps:

            client = ps.client('orders')

            order = await client.create_order(image_order)
            print(f"Placed order {order['id']}", flush=True)
            order_details = await self.wait_for_order(client, order['id'])

        pprint(order_details)
        return order_details

    async def previous_order(self, client, order_name, collection_name):
        """This day's newest order if it can still be used: in flight, or finished with its
        images still in the day collection. None means a new order is needed."""
        async for order in client.list_orders(name=order_name, limit=20):
            gee = order.get('delivery', {}).get('google_earth_engine')
            if gee and gee.get('collection') != collection_name:
                continue  # same name, delivered somewhere else (e.g. a manual order)
            if order['state'] in ('failed', 'cancelled') or ee.data.getInfo(self.target) is None:
                return None
            if order['state'] not in ORDER_IN_FLIGHT and not ee.data.listImages(self.target)['images']:
                return None  # its images were since deleted (e.g. reprocessing a finished day)
            return order
        return None

    async def wait_for_order(self, client, order_id):
        """Poll until the order reaches a final state and return its details. The state is
        printed every poll: a silent wait gets the CircleCI step killed after 30 min."""
        t_0 = time.monotonic()
        while True:
            order = await client.get_order(order_id)
            waited = time.monotonic() - t_0
            print(f"order {order_id}: {order['state']} ({waited / 60:.0f} min)", flush=True)
            if order['state'] not in ORDER_IN_FLIGHT:
                return order
            if waited >= self.order_timeout_s:
                # Leave the order running; the next run finds it with previous_order
                raise DayDeferred(f"Planet order {order_id} still {order['state']} after "
                                  f"{waited / 60:.0f} min; the next run picks it up")
            await asyncio.sleep(self.order_poll_s)

    def get_ImageCollections(self):
        # Search window = the Hawaii calendar day (self.begin/self.end, UTC ms). Converting
        # with an explicit UTC zone keeps it independent of the host's timezone; the old code
        # used naive local time labelled "+00:00", which on an MST host shifted the window
        # 7 h early, so each "day D" held imagery acquired on day D-1.

        self.planet = ee.ImageCollection(self.target)

        order_details = asyncio.run(
            self.order_and_push(
                order_name=datetime.fromtimestamp(self.begin / 1000, tz=timezone.utc).isoformat(),
                geojson_file=self.local_aoi_loc, 
                PLANET_API_KEY=self.planet_api_key, 
                t_start=datetime.fromtimestamp(self.begin / 1000, tz=timezone.utc).isoformat(),
                t_stop=datetime.fromtimestamp(self.end / 1000, tz=timezone.utc).isoformat(),
                item_type='PSScene', 
                cloud_cover=0.2,
                use_asset_filter=True, 
                asset_filter='ortho_analytic_8b_sr', #'ortho_analytic_4b_sr', 
                product_bundle='analytic_8b_sr_udm2',
                project_name=self.project,
                collection_name=f"{self.daily}/{self.date}",
                clip_with_geojson=True, 
                harmonize=False))

        if order_details is None:
            # nothing to order for this day
            return 0

        time.sleep(30)
        self.init_ee()
        self.planet = ee.ImageCollection(self.target)

        n_images = self.planet.size().getInfo()
        if n_images == 0:
            # The order was delivered but nothing landed in GEE yet; fail so the
            # day is retried rather than recorded as a day without imagery.
            raise RuntimeError(f"Planet order for {self.date} completed but {self.target} is empty")
        return n_images

    def diff_baseline(self):
        tmp = []

        dep_mask = ee.Image(self.dep.gt(2))

        self.planet_processed = self.planet# .select(['B1', 'B2', 'B3'])
        self.planet_processed = self.planet_processed.toList(100)
        for i in range(self.planet_processed.length().getInfo()):
            tmp.append(ee.Image(self.planet_processed.get(i)).clip(self.aoi))
        self.planet_processed = tmp
        
        # self.planet_processed = [image.updateMask(image.neq(0)) for image in self.planet_processed] # remove nodata corner pixels
        self.planet_processed = [image.reduceResolution(ee.Reducer.mean(), maxPixels=100).reproject(crs=image.projection(), scale=10) for image in self.planet_processed]
        self.planet_processed = [ee.Image(image.updateMask(dep_mask)) for image in self.planet_processed]
        self.planet_processed = [ee.Image(utils.mask_clouds_ps_not_clear_udm(image, confidence_threshold=70)) for image in self.planet_processed]
        self.planet_processed = [ee.Image(image.updateMask(image.select(['B5']).lt(image.select(['B2'])))) for image in self.planet_processed]
        self.planet_processed = [ee.Image(image.updateMask(image.select(['B3']).lt(500))) for image in self.planet_processed]
        self.planet_processed = [ee.Image(image.select(['B3', 'B2', 'B1']).subtract(image.select(['B5']))) for image in self.planet_processed]
        
        self.diff = [ee.Image(utils.subtract_other_image(image, self.baselines)) for image in self.planet_processed]
        
    def get_vectors(self):
        kernel = ee.Kernel.gaussian(radius=15, sigma=3, units="pixels")
        geom_Max_Error = 10
        area_Max_Error = 10
        min_Area = 5000
        min_diff = 12.5
        
        def outline(image):
            out = image.reduce('median').max(0)
            out = out.convolve(kernel).gt(min_diff)
            out = out.updateMask(out.neq(0)).int()
            out = out.reduceToVectors(geometry=self.aoi, bestEffort=True, 
                                            geometryType='polygon',
                                            eightConnected=False,
                                            labelProperty='zone')
            out = out.map(lambda f: f.set({'size': f.geometry(geom_Max_Error).area(area_Max_Error)})).filterMetadata('size', 'greater_than', min_Area)
            
            return out

        self.vectors = [outline(image) for image in self.diff]
        
        if (len(self.vectors) > 0):
            merged = self.vectors.pop(0)

        while (len(self.vectors)  > 0 ):
            merged = merged.merge(self.vectors.pop(0))

        self.vectors = merged

    def configure_deliverables(self):
        if self.vectors is not None:
            self.product = []
            # self.product.extend(self.diff)
            self.product.append(self.vectors)
        else:
            self.product = self.diff

    def cleanup(self):

        # Re-initialize because Planet->GEE takes forever
        self.init_ee()

        # No collection yet is the normal case on a day's first run. getInfo returns None
        # for it instead of raising, so any error from the deletes below is a real one.
        if ee.data.getInfo(self.target) is None:
            return

        # A collection must be empty before it can be deleted
        for image in ee.data.listImages(self.target)['images']:
            ee.data.deleteAsset(image['name'])
        ee.data.deleteAsset(self.target)
