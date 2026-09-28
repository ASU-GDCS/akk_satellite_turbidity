import ee

from . import utils
from .turbidity_monitor import TurbidityMonitor

class SentinelTurbidity(TurbidityMonitor):
    def __init__(self, 
                 credentials = None,
                 project: str = None,
                 date: str = None,
                 aoi_loc: str = None,
                 sentinel2_baselines_loc: str = None,
                 bucket:str = None,
                 prefix: str = None
                 ):
        
        # set instance variables
        self.name = "sentinel2"
        self.credentials = credentials
        self.project = project
        self.aoi_loc = aoi_loc
        self.date = date
        self.set_time_interval(date)
        self.bucket = bucket
        self.prefix = prefix

        # must init GEE before setting remaining variables
        self.init_ee()

        self.aoi = ee.FeatureCollection(aoi_loc)
        self.baselines = ee.ImageCollection(sentinel2_baselines_loc)

    def get_ImageCollections(self):
        self.sentinel2_harmonized_sr = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterDate(self.begin, self.end).filterBounds(self.aoi)
        self.sentinel2_cloud_proba   = ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY').filterDate(self.begin, self.end).filterBounds(self.aoi)
        self.sentinel2_sr            = utils.index_join(self.sentinel2_harmonized_sr, self.sentinel2_cloud_proba, 'cloud_probability') # Join collections

        return self.sentinel2_sr.size().getInfo()
    
    def diff_baseline(self):
        # Get number of observations available
        n_obs = self.sentinel2_sr.size().getInfo()

        # If there is at least one observation
        if n_obs > 0:
            # Get SPACECRAFT_NAME (there would never be observations from both satellites on the same day)
            self.spacecraft_name = self.sentinel2_sr.first().get('SPACECRAFT_NAME').getInfo()

            # apply filters
            self.sentinel2_sr_qc = self.sentinel2_sr.filter(ee.Filter.eq('SPACECRAFT_NAME', self.spacecraft_name))\
                                  .map(utils.mask_clouds_s2_probability)\
                                  .map(utils.mask_clouds_s2_qa)\
                                  .map(lambda image: utils.mask_image_by_expression(image, '(B2 < 0.1) && (B3 < 0.1) && (B4 < 0.1)'))\
                                  .map(lambda image: utils.mask_image_by_expression(image, 'B8 < B2'))

            # mask glint and compute avw and ndi
            self.sentinel2_sr_qc_gc = self.sentinel2_sr_qc.map(lambda image: utils.subtract_glint(image, glint_band='B8', band_selection='B.*'))\
                                              .map(lambda image: utils.add_avw(image, self.spacecraft_name))\
                                              .map(lambda image: utils.add_ndi(image, 'B4', 'B2', 'NDI'))
            
            # or use pre-computed baseline
            self.sentinel2_sr_qc_gc_ts_median = self.baselines.filter(ee.Filter.stringContains('system:index', '2023_median_float')).first().clip(self.aoi)

            # mosaic Images in ImageCollection and set projection
            self.sentinel2_sr_qc_gc_mosaic = self.sentinel2_sr_qc_gc.mosaic().clip(self.aoi).reproject(self.sentinel2_sr_qc_gc_ts_median.projection())

            # subtract baseline from mosaic
            self.sentinel2_sr_qc_gc_diff_to_median = self.sentinel2_sr_qc_gc_mosaic.subtract(self.sentinel2_sr_qc_gc_ts_median)

            self.sentinel2_sr_qc_gc_diff_to_median = self.sentinel2_sr_qc_gc_diff_to_median.addBands(self.sentinel2_sr_qc_gc_mosaic.select('B2', 'B3', 'B4'))

            # return self.sentinel2_sr_qc_gc_diff_to_median.reproject(self.sentinel2_sr_qc_gc_ts_median.projection(), scale=30)
        
    def get_vectors(self):
        kernel = ee.Kernel.gaussian(radius=15, sigma=3, units="pixels")

        zones = self.sentinel2_sr_qc_gc_diff_to_median.clipToCollection(self.aoi).select(['B2', 'B3', 'B4']).reduce('median').max(0)
        zones = zones.convolve(kernel).gt(0.0125)
        zones = zones.updateMask(zones.neq(0))

        zones = zones.reduceToVectors(geometry=self.aoi, bestEffort=True, 
                                             geometryType='polygon',
                                             eightConnected=False,
                                             labelProperty='zone')
        # filter out small polygons
        geom_Max_Error = 10
        area_Max_Error = 10
        min_Area = 5000
        zones = zones.map(lambda f: f.set({'size': f.geometry(geom_Max_Error).area(area_Max_Error)})).filterMetadata('size', 'greater_than', min_Area)
        
        self.vectors = zones

    def configure_deliverables(self):
        if self.vectors is not None:
            self.product = [self.sentinel2_sr_qc_gc_diff_to_median, self.vectors]
        else:
            self.product = [self.sentinel2_sr_qc_gc_diff_to_median]
