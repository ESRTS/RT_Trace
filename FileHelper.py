import sys
import os
import platform

def getCwd():
    """
    Get the current working directory, this is different on OSX in the app version.  
    """
    if platform.system() == "Darwin":
        return os.path.sep.join(sys.argv[0].split(os.path.sep)[:-1])
    else:
        return os.getCwd()
    
def getConfigFilePath():
    """
    Get the path to the configuration file.
    """
    cwd = getCwd()

    # Use thos to export the application
    return os.path.abspath(os.path.join(os.path.dirname( cwd ), 'Resources', 'config.ini'))

    # Use this for development
    #return os.path.abspath(os.path.join(cwd, 'Resources', 'config.ini'))
