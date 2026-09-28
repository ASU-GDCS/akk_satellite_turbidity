#  This file is a collection of useful functions that can be called from other scripts.

import re 
import ee


def index_join(collectionA, collectionB, property_name):
    # Perform the join using 'system:index' property
    joined = ee.ImageCollection(ee.Join.saveFirst(property_name).apply(
        primary=collectionA,
        secondary=collectionB,
        condition=ee.Filter.equals(leftField='system:index', rightField='system:index')
    ))

    # Merge the bands of the joined image
    def merge_bands(image):
        return image.addBands(ee.Image(image.get(property_name)))

    # Map the merge_bands function over the joined collection
    return joined.map(merge_bands)


def compute_avw(image, sensor):
    """
    Compute apparent visible wavelength (AVW) for selected sensors following Vandermeulen (2022) [https://oceancolor.gsfc.nasa.gov/atbd/avw/]. 
    !!! Thanks for the PSD cofficients, Ryan !!!

    Note that the band notation changes by sensor!
    """
    # Define c and avw outside of if-clauses so they will be accessible later on
    c, avw = None, None
    
    # Compute avw depending on sensor
    if sensor == 'OLI':
        c = [-7.5487887E-09, 1.9136261E-05, -1.9333568E-02, 9.7261770E+00, -2.4338650E+03, 2.4247497E+05]
        avw = image.select(['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4']).reduce(ee.Reducer.sum()) \
            .divide(image.select(['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4']).divide([443, 482, 561, 655]).reduce(ee.Reducer.sum()))
    elif sensor == 'Sentinel-2A':
        c = [-7.4719643E-10, 1.8794584E-06, -1.8924228E-03, 9.5069314E-01, -2.3623942E+02, 2.3384674E+04]
        avw = image.select(['B1', 'B2', 'B3', 'B4']).reduce(ee.Reducer.sum()) \
            .divide(image.select(['B1', 'B2', 'B3', 'B4']).divide([443, 490, 560, 665]).reduce(ee.Reducer.sum()))
    elif sensor == 'Sentinel-2B':
        c = [-1.3572502E-09, 3.4546589E-06, -3.5159381E-03, 1.7855878E+00, -4.5046399E+02, 4.5327899E+04]
        avw = image.select(['B1', 'B2', 'B3', 'B4']).reduce(ee.Reducer.sum()) \
            .divide(image.select(['B1', 'B2', 'B3', 'B4']).divide([443, 490, 559, 665]).reduce(ee.Reducer.sum()))
    elif sensor == 'PSD':
        c = [-3.1517567102E-10, 1.1191021004E-06, -1.4907044865E-03, 9.5281345072E-01, -2.9453261740E+02, 3.5790861208E+04]
        avw = image.select(['B1', 'B2', 'B3', 'B4', 'B5', 'B6']).reduce(ee.Reducer.sum()) \
            .divide(image.select(['B1', 'B2', 'B3', 'B4', 'B5', 'B6']).divide([444, 492, 533, 566, 612, 666]).reduce(ee.Reducer.sum()))
        
    # Compute hyperspectral AVW
    avw_cal = avw.expression('c[0]*(avw**5) + c[1]*(avw**4) + c[2]*(avw**3) + c[3]*(avw**2) + c[4]*(avw) + c[5]', {
        'c': c,
        'avw': avw
    })

    return avw_cal.toFloat()


def add_avw(image, sensor):
    """
    Compute AVW and add as new band.
    """
    avw = compute_avw(image, sensor).rename('AVW')
    return image.addBands(avw)


def add_ndi(image, b1='SR_B4', b2='SR_B2', new_band_name='NDI'):
    """
    Compute normalized difference index and add as new band.
    """
    ndi = image.normalizedDifference([b1, b2]).rename(new_band_name)
    return image.addBands(ndi)


def subtract_glint(image, glint_band='SR_B5', band_selection='SR_B.*'):
    """
    Simple glint correction assuming black NIR.
    """
    # Select the glint band
    glint = image.select(glint_band)
    
    # Select all bands, subtract glint, and copy properties
    corrected_image = image.select([band_selection]).subtract(glint) \
                        .copyProperties(image) \
                        .set('system:time_start', image.get('system:time_start'))
    
    return corrected_image


def mask_clouds_landsat_qa(image):
    """
    Cloud mask based on the Landsat 8 and 9 QA bitmask band.
    """
    # Bit 0 - Fill
    # Bit 1 - Dilated Cloud
    # Bit 2 - Cirrus
    # Bit 3 - Cloud
    # Bit 4 - Cloud Shadow
    
    # Select the QA_PIXEL band
    qa_mask = image.select('QA_PIXEL').bitwiseAnd(ee.Number(2).pow(0).toInt()
                                                    .add(ee.Number(2).pow(1)).toInt()
                                                    .add(ee.Number(2).pow(2)).toInt()
                                                    .add(ee.Number(2).pow(3)).toInt()
                                                    .add(ee.Number(2).pow(4)).toInt()).eq(0)
    
    # Select the QA_RADSAT band
    saturation_mask = image.select('QA_RADSAT').eq(0)
    
    # Combine the masks
    combined_mask = qa_mask.Or(saturation_mask)

    # Apply the scaling factors to the appropriate bands.
    optical_bands = image.select('SR_B.').multiply(0.0000275).add(-0.2)
    thermal_bands = image.select('ST_B.*').multiply(0.00341802).add(149.0)

    # Replace the original bands with the scaled ones and apply the masks.
    return image.addBands(optical_bands, None, True)\
                .addBands(thermal_bands, None, True)\
                .updateMask(combined_mask)


def mask_clouds_ps_not_clear_udm(image, confidence_threshold=70):
    """
    Cloud mask based on planet's udm2 and confidence bands.
    """
    clear = image.select('Q1')
    confidence = image.select('Q7')
    
    # Mask pixels that are not "clear with high confidence (>X%)"
    mask = clear.eq(1)
    mask.updateMask(confidence.gt(confidence_threshold))
    
    # Return the masked image
    return image.updateMask(mask)


def mask_clouds_s2_qa(image):
    qa = image.select('QA60')

    # Bits 10 and 11 are clouds and cirrus, respectively.
    cloud_bit_mask = 1 << 10
    cirrus_bit_mask = 1 << 11

    # Both flags should be set to zero, indicating clear conditions.
    mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(
        qa.bitwiseAnd(cirrus_bit_mask).eq(0)
    )

    # Return the masked and scaled data, without the QA bands.
    return image.updateMask(mask)\
        .divide(10000)\
        .select(["B.*"])\
        .copyProperties(image, ["system:time_start"])


def mask_clouds_s2_probability(image, threshold=5):
    cloud_proba = image.select('probability')
        
    # Create a cloud mask using the threshold
    cloud_mask = cloud_proba.lt(threshold)
    
    # Return the image with the applied cloud mask
    return image.updateMask(cloud_mask)


def mask_image_by_expression(image, expression):
    # Extract band names from the expression
    band_names = [band.strip() for band in re.findall(r'\b\w+\b', expression)]

    # Create a dictionary to hold band selectors
    band_selectors = {band: image.select(band) for band in band_names}

    # Evaluate the expression with dynamic band selectors
    mask = image.expression(expression, band_selectors)

    # Update the mask of the original image
    return image.updateMask(mask)


def subtract_other_image(image, other_image):
    result = image.subtract(other_image) \
        .copyProperties(image) \
        .set('system:time_start', image.get('system:time_start'))
    return result


def to_reflectance(image, factor=10000):
    # Convert image bands to floating-point format
    image_float = image#.toFloat()

    # Divide each pixel value by 10000 to convert to reflectance
    reflectance_image = image_float.divide(factor)

    # Copy properties from the original image
    reflectance_image = reflectance_image.copyProperties(image)

    # Set the 'system:time_start' property
    reflectance_image = reflectance_image.set('system:time_start', image.get('system:time_start'))

    return reflectance_image