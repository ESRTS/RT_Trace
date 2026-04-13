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
        return os.getcwd()
    
def getConfigFilePath():
    """
    Get the path to the configuration file.
    """
    cwd = getCwd()

    return os.path.abspath(os.path.join(os.path.dirname( cwd ), 'Resources', 'config.ini'))

def printState(state: str, info=""):
    
    inner = "\r\n=> " + state.upper() + " " + info
    print( inner ) 

def printHeader(header:str):
    width = 70

    assert len(header) <= width - 4, "Header string too long for the configured width of the text in printHeader()" + header

    inner = "┃ " + header.upper().ljust(width - 4) + " ┃" 
    top = "\r\n┏" + "━" * (width - 2) + "┓"
    bottom = "┗" + "━" * (width - 2) + "┛"

    print( "\n".join([top, inner, bottom]) )  

def hexdump(buffer: bytes, base_addr: int = 0, width: int = 16):
        for offset in range(0, len(buffer), width):
            chunk = buffer[offset:offset + width]

            # Address
            addr = base_addr + offset

            # Hex part
            hex_bytes = ' '.join(f'{b:02x}' for b in chunk)
            hex_bytes = hex_bytes.ljust(width * 3 - 1)

            # ASCII part (optional but useful)
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)

            print(f"0x{addr:08x}  {hex_bytes}  |{ascii_part}|") 

