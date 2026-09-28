import numpy as np
import planet
import ee

from . import utils
from .turbidity_monitor import TurbidityMonitor

import asyncio
from datetime import datetime, timezone
import json
import os
from pprint import pprint
import requests
from requests.auth import HTTPBasicAuth
import time

class PlanetTurbidity(TurbidityMonitor):
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

        try:
            ee.data.deleteAsset(f"{self.assets_root}/{collection_name}")
        except Exception as e:
            print(e)
    
        ee.data.createAsset({'type': 'ImageCollection'}, path=f"{self.assets_root}/{collection_name}")

        async  with planet.Session(auth=auth) as ps:

            client = ps.client('orders')

            with planet.reporting.StateBar(state='creating') as reporter:
                # Place an order to the Orders API
                order = await client.create_order(image_order)
                reporter.update(state='created', order_id=order['id'])
                # Wait while the order is being completed
                await client.wait(order['id'],
                                callback=reporter.update_state,
                                max_attempts=0)
            # Grab the details of the orders
            order_details = await client.get_order(order_id=order['id'])

        pprint(order_details)
        return order_details

    def get_ImageCollections(self):
        
        self.planet = ee.ImageCollection(self.target)
        self.cleanup()

        order_details = asyncio.run(
            self.order_and_push(
                order_name=f'{datetime.fromtimestamp(self.begin / 1000).isoformat()}+00:00', 
                geojson_file=self.local_aoi_loc, 
                PLANET_API_KEY=self.planet_api_key, 
                t_start=f'{datetime.fromtimestamp(self.begin / 1000).isoformat()}+00:00', 
                t_stop=f'{datetime.fromtimestamp(self.end / 1000).isoformat()}+00:00',
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

        # TODO: If asset exists AND not empty then skip
        # elif asset exists AND empty, delete asset then start order
        # else start order
        if (self.planet):
            # Re-initialize because Planet->GEE takes forever
            self.init_ee()

            n = None
            try:
                n = self.planet.size().getInfo()

                if n > 0:
                    # Get list of images in collection
                    image_list = self.planet.toList(n).getInfo()

                    for i in np.arange(n):
                        # Get image path
                        image_path = image_list[i]['id']
                        # Delete image from asset without confirmation
                        ee.data.deleteAsset(image_path)
            except Exception as e:
                print(e)
                
            try:
                ee.data.deleteAsset(self.planet.getInfo()['id'])
            except Exception as e:
                print(e)
