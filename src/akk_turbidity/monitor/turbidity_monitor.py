from abc import ABCMeta, abstractmethod
from datetime import datetime, timezone
import time
import sys

import ee


class ExportError(RuntimeError):
    pass


class DayDeferred(Exception):
    """The day isn't finished but nothing failed (e.g. a Planet order still being
    delivered); it stays unmarked and the next run continues it."""


class TurbidityMonitor(metaclass=ABCMeta):
    date: str       = None
    begin: str      = None
    end: str        = None
    timestamp: str  = None
    name: str       = None
    vectors         = None
    credentials     = None
    project: str    = None

    @abstractmethod
    def __init__(self):
        pass

    def init_ee(self):
        ee.Initialize(credentials=self.credentials, project=self.project)

    def set_time_interval(self, date:str=None):
        self.YYYY, self.MM, self.DD = [int(part) for part in date.split("-")] # Naive time
        unix_time_seconds = int(datetime(self.YYYY, self.MM, self.DD, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        self.begin: int = 1000 * (unix_time_seconds - 36000) # HI is 10hrs behind UTC. Daylight savings not observed.
        self.end: int =   (self.begin + 86400000)

    def last_im_time(self, imcoll:ee.ImageCollection):
        sorted_ims = imcoll.sort('system:time_start', False)
        last_im = sorted_ims.first()
        im_date = last_im.date() 
        year   = im_date.get('year').getInfo()
        month  = f"{im_date.get('month').getInfo():02d}"
        day    = f"{im_date.get('day').getInfo():02d}"
        hour   = f"{im_date.get('hour').getInfo():02d}"
        minute = f"{im_date.get('minute').getInfo():02d}"
        second = f"{im_date.get('second').getInfo():02d}"

        return f'{year}-{month}-{day}T{hour}:{minute}:{second}.000Z'

    @abstractmethod
    def get_ImageCollections(self):
        pass

    @abstractmethod
    def diff_baseline(self):
        pass

    @abstractmethod
    def configure_deliverables(self):
        # set self.product = self.<whatever image you want from meas_turbidity>
        pass

    @abstractmethod
    def get_vectors(self):
        pass

    # Earth Engine runs only a couple of export tasks at a time per project, so a task
    # can sit queued (READY) for a while when several jobs export at once.
    export_queue_timeout_s = 1800
    export_run_timeout_s = 2400

    def _wait_for_export(self, res, exp_type):
        time.sleep(10)
        t_0 = time.time()
        # wait for export and timeout
        while( (time.time() - t_0 < self.export_queue_timeout_s) and (res.status()['state'] == "READY")):
                print(f"{self.name}(ready): {int(time.time() - t_0):04d}", end="\r")
                time.sleep(10)
        t_0 = time.time()
        while( (time.time() - t_0 < self.export_run_timeout_s) and (res.status()['state'] == "RUNNING")):
                print(f"{self.name}({exp_type}): {int(time.time() - t_0):04d}", end="\r")
                sys.stdout.flush()
                time.sleep(10)
        print()

        # Read the status once, while Earth Engine is still initialised; reading it after
        # ee.Reset() replaced the real reason with "client library not initialized".
        status = res.status()
        if status['state'] != "COMPLETED":
            print(status)
            if status['state'] in ("READY", "RUNNING"):
                # Giving up: cancel, so the task can't write outputs later for a day that
                # has no marker (and is then exported again on retry).
                try:
                    res.cancel()
                except Exception as exc:
                    print(f"Could not cancel {status.get('id')}: {exc}")
            ee.Reset()
            raise ExportError(f"Failed to export {exp_type} for {self.name} {self.date}: "
                              f"task {status.get('id')} ended in state {status['state']} "
                              f"{status.get('error_message', '')}".rstrip())

    def deliver_products(self):
        # Export failures used to be printed and ignored, which advanced the
        # timestamp past days whose outlines never reached the bucket. They are
        # now collected and re-raised so the day is retried on the next run.
        errors = []
        if len(self.product) > 0:
            for product_index in range(len(self.product)):
                # start export
                try:
                    self.init_ee()
                    if (type(self.product[product_index]) == ee.image.Image):
                        exp_type = "raster"
                        res = ee.batch.Export.image.toCloudStorage(self.product[product_index], bucket=self.bucket, fileNamePrefix=f"{self.prefix}{self.YYYY}/raster/{self.date}_{product_index}", region=self.aoi.geometry())
                        res.start()
                        self._wait_for_export(res, exp_type)
                    elif (type(self.product[product_index]) == ee.featurecollection.FeatureCollection):
                        exp_type = "vector"
                        res = ee.batch.Export.table.toCloudStorage(self.product[product_index], bucket=self.bucket, fileNamePrefix=f"{self.prefix}{self.YYYY}/vector/{self.date}", fileFormat="GeoJSON")
                        res.start()
                        self._wait_for_export(res, exp_type)
                except Exception as e:
                    print(e)
                    errors.append(e)
            ee.Reset()
        else:
            print(f'No output product assigned for {self.name}')
        if errors:
            raise ExportError(f"{len(errors)} export(s) failed for {self.name} {self.date}: {errors[0]}")

    def cleanup(self):
        pass

    def run_batch(self):
        """Process self.date. Returns the number of images found (0 = no data)."""
        n_images = self.get_ImageCollections()
        if n_images:
            self.diff_baseline()
            self.get_vectors()
            self.configure_deliverables()
            self.deliver_products()
            self.cleanup()
            self.timestamp = self.date
        return int(n_images or 0)
