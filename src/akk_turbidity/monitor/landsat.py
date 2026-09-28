import ee

from . import utils
from .turbidity_monitor import TurbidityMonitor

class LandsatTurbidity(TurbidityMonitor):
    def __init__(self, 
                 credentials = None,
                 project: str = None,
                 date: str = None,
                 aoi_loc: str = None,
                 landsat_baselines_loc: str = None,
                 bucket:str = None,
                 prefix: str = None
                 ):
        
        # set instance variables
        self.name = "landsat"
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
        self.baselines = ee.ImageCollection(landsat_baselines_loc)

    def get_ImageCollections(self):
        self.landsat8_sr = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
        self.landsat9_sr = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2')

        # manually excluded dates are an eruption fo Mauna Loa causing vog
        self.landsat_sr = self.landsat8_sr.filterDate(self.begin, self.end).merge(self.landsat9_sr.filterDate(ee.Date(self.begin), ee.Date(self.end))) \
                                .filter((ee.Filter.date('2022-12-01', '2022-12-03').Not())) \
                                .filterBounds(self.aoi)
        
        return self.landsat_sr.size().getInfo()
        
    def diff_baseline(self):
        # Get number of observations available
        n_obs = self.landsat_sr.size().getInfo()

        # If there is at least on observation
        if n_obs > 0:
            # apply filters to landsat_sr for qc
            self.landsat_sr_qc = self.landsat_sr.map(utils.mask_clouds_landsat_qa)\
                                    .map(lambda image: utils.mask_image_by_expression(image, '(SR_B2 < 0.1) && (SR_B3 < 0.1) && (SR_B4 < 0.1)'))\
                                    .map(lambda image: utils.mask_image_by_expression(image, 'SR_B5 < SR_B2'))
            # mask glint and compute avw and ndi
            self.landsat_sr_qc_gc = self.landsat_sr_qc.map(lambda image: utils.subtract_glint(image, glint_band='SR_B5', band_selection='SR_B.*'))\
                                            .map(lambda image: utils.add_avw(image, 'OLI'))\
                                            .map(lambda image: utils.add_ndi(image, 'SR_B4', 'SR_B2', 'NDI'))
            # use pre-computed baseline from 2022
            self.landsat_sr_qc_gc_ts_median = self.baselines.filter(ee.Filter.stringContains('system:index', '2023')).first()
            # mosaic Images in ImageCollection and set projection
            self.landsat_sr_qc_gc_mosaic = self.landsat_sr_qc_gc.mosaic().reproject(self.landsat_sr_qc_gc_ts_median.projection()).clip(self.aoi)
            # subtract baseline from mosaic
            self.landsat_sr_qc_gc_diff_to_median = self.landsat_sr_qc_gc_mosaic.subtract(self.landsat_sr_qc_gc_ts_median)

            self.landsat_sr_qc_gc_diff_to_median = self.landsat_sr_qc_gc_diff_to_median.addBands(self.landsat_sr_qc_gc_mosaic.select('SR_B2', 'SR_B3', 'SR_B4'))

            return self.landsat_sr_qc_gc_diff_to_median

    def get_vectors(self):
        kernel = ee.Kernel.gaussian(radius=5, sigma=1, units="pixels")
        
        zones = self.landsat_sr_qc_gc_diff_to_median.select(['SR_B2', 'SR_B3', 'SR_B4']).reduce('median').max(0)
        zones = zones.convolve(kernel).gt(0.0125).clipToCollection(self.aoi)
        zones = zones.updateMask(zones.neq(0))

        zones = zones.reduceToVectors(geometry=self.aoi, bestEffort=True, 
                                             geometryType='polygon',
                                             eightConnected=False,
                                             labelProperty='zone')
        
        # filter out small polygons
        geom_Max_Error = 10
        area_Max_Error = 10
        min_Area = 8000
        zones = zones.map(lambda f: f.set({'size': f.geometry(geom_Max_Error).area(area_Max_Error)})).filterMetadata('size', 'greater_than', min_Area)

        self.vectors = zones

    def configure_deliverables(self):
        if self.vectors is not None:
            self.product = [self.landsat_sr_qc_gc_diff_to_median.toFloat(), self.vectors]
        else:
            self.product = [self.landsat_sr_qc_gc_diff_to_median.toFloat()]
