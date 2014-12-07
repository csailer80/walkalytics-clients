import os 
import json
import re
import gzip
import StringIO
import netrc 
import urllib
import urllib2

import arcpy
import numpy as np
try:
    from  PIL import Image # get pillow from here: http://www.lfd.uci.edu/~gohlke/pythonlibs/#pillow
except ImportError:
    raise arcpy.ExecuteError("Python module PIL is not available")


# - set the api_key by reading a netrc file with API key. 
try:
    path = os.path.dirname(__file__)
    secrets = netrc.netrc("{}/walkalytics-api-key".format(path))
    _, _, api_key = secrets.authenticators("api.walkalytics.com")
except IOError:
    api_key = "<Get key from dev.walkalytics.com>"
    pass

def encode_base64(s):
    # - do a regexp match for extension, code and actual data (this is slightly
    #   more general than necessary)
    pattern = re.compile("^data:.+\/(.+);(.+),(.*)$")
    match = pattern.search(s)
    # - extract extension, coding and actual data
    extension = match.group(1)
    code = match.group(2) 
    data = match.group(3)
    assert(code == "base64") # make sure we have base64
    return data.decode(code)



def decode_img(s):
    # - do a regexp match for extension, code and actual data (this is slightly
    #   more general than necessary)
    pattern = re.compile("^data:.+\/(.+);(.+),(.*)$")
    match = pattern.search(s)
    # - extract extension, coding and actual data
    extension = match.group(1)
    code = match.group(2) 
    data = match.group(3)
    assert(code == "base64") # make sure we have base64
    assert(extension == "png") # make sure we have a PNG file

    return data.decode(code)


def call_walkalytics(x,y,epsg_code,raw_data,api_key,messages):
    if raw_data:
        raw_data = 'true'
    else:
        raw_data = 'false'
    # - set API parameters and set of POIs
    params = {
        'x' : x,
        'y' : y,
        'epsg': epsg_code,
        "only_pois": 'false', # or "true"
        "outputsize": 512,    # max is currently 720
        'raw_data': raw_data,
        # more parameters are possible, see API documentation
        'key': api_key
    }

    pois = { }

    # - define API call URL
    url = 'https://api.walkalytics.com/v1/isochrone'

    messages.AddMessage("Calling Walkalytics for ({},{})".format(x,y))
    # Now make the actual API call as a POST request
    try:
        req = urllib2.Request("{0}?{1}".format(url,urllib.urlencode(params)), 
                              json.dumps(pois),
                              {'Content-Type': 'application/json'})
        response = urllib2.urlopen(req)
        result = json.loads(response.read())
    except urllib2.HTTPError, e:
        if e.code == 204:
            pass
        else:
            messages.AddMessage("Error with request: {0}".format(e))
            return None

    # Check status
    if result.get("status") != "success":
        messages.AddMessage("API call did not succeed. Details: {}".format(result.get("msg")))
        return None

    messages.AddMessage("Done calling Walkalytics API")
    return result


class Toolbox(object):
    def __init__(self):
        """Define the toolbox (the name of the toolbox is the name of the
        .pyt file)."""
        self.label = "Walkalytics Toolbox"
        self.alias = "Walkalytics"
        self.description = "A set of tools to use Walkalytics in ArcGIS Desktop."

        # List of tool classes associated with this toolbox
        self.tools = [Isochrone, IsochroneRaw]


class Isochrone(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "Isochrone"
        self.description = "Calculates classified walking isochrone for source location(s)."
        self.canRunInBackground = False

        self.sources_fc_name = "source_locations"


        self.sr3857 = arcpy.SpatialReference()
        self.sr3857.factoryCode = 3857
        self.sr3857.create()
            
    def getParameterInfo(self):
        """Define parameter definitions"""
        param0 = arcpy.Parameter(
            displayName="Source Location",
            name=self.sources_fc_name,
            datatype="GPFeatureRecordSetLayer",
            parameterType="Required",
            direction="Input")
        param0.value = self.sources_fc_name

        param1 = arcpy.Parameter(
            displayName="Raster Outputpath",
            name="out_workspace",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input")

        # specify APIkey
        param2 = arcpy.Parameter(
            displayName="Walkalytics API key",
            name="apikey",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        param2.value=api_key
        
        
        params = [param0, param1, param2]
        return params

    def isLicensed(self):
        """Set whether tool is licensed to execute."""
        return True

    def updateParameters(self, parameters):
        """Modify the values and properties of parameters before internal
        validation is performed.  This method is called whenever a parameter
        has been changed."""
        return

    def updateMessages(self, parameters):
        """Modify the messages created by internal validation for each tool
        parameter.  This method is called after internal validation."""
        return

    def execute(self, parameters, messages):
        """ Executing Walkalytics API."""
        inFeatures      = parameters[0].valueAsText
        # get epsg id for inFeatures
        sr = arcpy.Describe(inFeatures).spatialReference
        epsg_code = sr.factoryCode
        arcpy.env.overwriteOutput = True
        for row in arcpy.da.SearchCursor(inFeatures, ["SHAPE@XY"]):
            x, y = row[0]
            result = call_walkalytics(x,y,epsg_code,False,api_key,messages)
            png_blob = encode_base64(result['img']) # decode_img(...)
            
            # convert the PNG blob to an indexed image with values from 0 to 7.
            imageRGBA = Image.open(StringIO.StringIO(png_blob))
            alpha_channel = np.asarray(imageRGBA.split()[-1])
            isochrone_array = np.asarray(imageRGBA.convert("P"))
            no_data_index = 255
            isochrone_array = np.where(alpha_channel == 0, no_data_index , 8 - isochrone_array)
            
            lower_left_corner = arcpy.Point(result["xllcorner"], result["yllcorner"])
            (x_cell_size,y_cell_size) = (result["cellsize"], result["cellsize"])
            value_to_nodata = no_data_index # nodata is alpha channel == 0 in PNG

            ## Convert array to a geodatabase raster
            isochrone_raster = arcpy.NumPyArrayToRaster(isochrone_array, lower_left_corner, 
                                                        x_cell_size, y_cell_size, value_to_nodata)
            imagename = "isochrone_{0}_{1}".format(int(x),int(y))
            messages.addMessage("Save isochrone as {}".format(imagename))
            isochrone_raster.save("{}/{}".format(parameters[1].valueAsText,imagename))
            # Coordinate system is always EPSG3857 (Web Mercator)
            arcpy.DefineProjection_management(isochrone_raster, self.sr3857)
            # Add the default colorbrewer colormap to this image
            arcpy.AddColormap_management(isochrone_raster, "#", "colorbrewer.clr")
        return True


class IsochroneRaw(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "Isochrone Raw"
        self.description = "Calculates walking isochrone with exact minutes for every pixel for source location(s)."
        self.canRunInBackground = False

        self.sources_fc_name = "source_locations"


        self.sr3857 = arcpy.SpatialReference()
        self.sr3857.factoryCode = 3857
        self.sr3857.create()
            
    def getParameterInfo(self):
        """Define parameter definitions"""
        param0 = arcpy.Parameter(
            displayName="Source Location",
            name=self.sources_fc_name,
            datatype="GPFeatureRecordSetLayer",
            parameterType="Required",
            direction="Input")
        param0.value = self.sources_fc_name

        param1 = arcpy.Parameter(
            displayName="Raster Outputpath",
            name="out_workspace",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input")

        # specify APIkey
        param2 = arcpy.Parameter(
            displayName="Walkalytics API key",
            name="apikey",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        param2.value=api_key
        
        
        params = [param0, param1, param2]
        return params

    def isLicensed(self):
        """Set whether tool is licensed to execute."""
        return True

    def updateParameters(self, parameters):
        """Modify the values and properties of parameters before internal
        validation is performed.  This method is called whenever a parameter
        has been changed."""
        return

    def updateMessages(self, parameters):
        """Modify the messages created by internal validation for each tool
        parameter.  This method is called after internal validation."""
        return

    def execute(self, parameters, messages):
        """ Executing Walkalytics API."""
        inFeatures      = parameters[0].valueAsText
        # get epsg id for inFeatures
        sr = arcpy.Describe(inFeatures).spatialReference
        epsg_code = sr.factoryCode
        arcpy.env.overwriteOutput = True
        for row in arcpy.da.SearchCursor(inFeatures, ["SHAPE@XY"]):
            x, y = row[0]
            result = call_walkalytics(x,y,epsg_code,True,api_key,messages)
            # base64 decoding
            asc_gz_blob = encode_base64(result['raw_data'])

            # decompress gzip
            fileobj = StringIO.StringIO(asc_gz_blob)
            gzf = gzip.GzipFile('dummy-name', 'rb', 9, fileobj)
            blob = gzf.read()
            lines = blob.split('\n')[0:-1]
            gzf.close()
            
            
            # convert Esri Ascii grid to numpy
            celltype = int
            nrows     = int(lines[0].split()[1].strip())
            ncols     = int(lines[1].split()[1].strip())
            xllcorner = int(lines[2].split()[1].strip())
            yllcorner = int(lines[3].split()[1].strip())
            lower_left_corner = arcpy.Point(xllcorner, yllcorner)
            cellsize  = float(lines[4].split()[1].strip())
            nodata_value  = celltype(float(lines[5].split()[1].strip()))
            fileobj = StringIO.StringIO("\n".join(lines[6:]))
            isochrone_array = np.loadtxt(fileobj,dtype=celltype) 

            ## Convert array to a geodatabase raster
            isochrone_raster = arcpy.NumPyArrayToRaster(isochrone_array, lower_left_corner, 
                                                        cellsize, cellsize, nodata_value)
            imagename = "isochrone_raw_{0}_{1}".format(int(x),int(y))
            messages.addMessage("Save isochrone as {}".format(imagename))
            # Coordinate system is always EPSG3857 (Web Mercator)
            arcpy.DefineProjection_management(isochrone_raster, self.sr3857)
            isochrone_raster.save("{}/{}".format(parameters[1].valueAsText,imagename))
        return True
