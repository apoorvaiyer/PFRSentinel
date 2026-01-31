"""
Windows USB Device Reset Utility
=================================

Provides USB device reset functionality for recovering ZWO cameras that get stuck
in a bad state after exposure failures or long-duration captures.

This module uses Windows Device Management API (SetupAPI + cfgmgr32) to:
1. Enumerate USB devices by vendor/product ID (ZWO = VID_03C3)
2. Request device restart via CM_Request_Device_Eject
3. Re-enable the device to force Windows driver reload

IMPORTANT: Requires Administrator privileges for device restart.
"""

import ctypes
import ctypes.wintypes as wintypes
from typing import Optional, List
import time


# Windows constants
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
DIGCF_ALLCLASSES = 0x00000004
INVALID_HANDLE_VALUE = -1
ERROR_NO_MORE_ITEMS = 259

# Device restart/eject constants  
CR_SUCCESS = 0x00000000
CM_LOCATE_DEVNODE_NORMAL = 0x00000000
CM_REENUMERATE_NORMAL = 0x00000000

# ZWO Camera USB identifiers
ZWO_USB_VID = 0x03C3  # ZWO Vendor ID

# Device Class GUIDs
# Imaging devices: {6BDD1FC6-810F-11D0-BEC7-08002BE2092F}
GUID_DEVCLASS_IMAGE = b'\xc6\x1f\xdd\x6b\x0f\x81\xd0\x11\xbe\xc7\x08\x00\x2b\xe2\x09\x2f'
# USB devices: {36FC9E60-C465-11CF-8056-444553540000}
GUID_DEVCLASS_USB = b'\x60\x9e\xfc\x36\x65\xc4\xcf\x11\x80\x56\x44\x45\x53\x54\x00\x00'


# SetupAPI structures
class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", wintypes.BYTE * 16),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", wintypes.BYTE * 16),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


# Load Windows DLLs
try:
    setupapi = ctypes.WinDLL('setupapi', use_last_error=True)
    cfgmgr32 = ctypes.WinDLL('cfgmgr32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    
    WINDOWS_API_AVAILABLE = True
except Exception:
    WINDOWS_API_AVAILABLE = False


def reset_zwo_camera_usb(camera_name: Optional[str] = None, logger=None) -> bool:
    """
    Reset USB connection for ZWO camera by requesting device re-enumeration.
    
    This forces Windows to reload the USB driver, which can recover cameras
    stuck in bad states after exposure failures.
    
    Args:
        camera_name: Optional camera name (e.g., "ZWO ASI676MC") for targeted reset.
                    If None, resets ALL ZWO cameras.
        logger: Optional logger callback function
        
    Returns:
        True if reset was attempted, False if not available
        
    Note:
        - May require Administrator privileges
        - Camera will be unavailable for ~2-5 seconds during reset
        - SDK must be reinitialized after reset
    """
    def log(msg):
        if logger:
            logger(msg)
        else:
            print(msg)
    
    if not WINDOWS_API_AVAILABLE:
        log("⚠ Windows API not available - USB reset not supported on this system")
        return False
    
    log("=== Attempting USB Device Reset ===")
    
    try:
        # Try to use SDK to get actual camera list first
        # This gives us the real camera names to search for in Device Manager
        try:
            import zwoasi as asi
            asi.init('ASICamera2.dll')
            num_cameras = asi.get_num_cameras()
            if num_cameras > 0:
                camera_list = asi.list_cameras()
                log(f"ZWO SDK reports {num_cameras} camera(s): {camera_list}")
                
                # Use SDK camera names to search Windows Device Manager
                for cam_name in camera_list:
                    if camera_name and camera_name not in cam_name:
                        continue  # Skip if looking for specific camera
                    
                    if _reset_device_by_description(cam_name, logger):
                        log(f"✓ Reset successful for: {cam_name}")
                        return True
                
                log("⚠ SDK found cameras but Device Manager reset failed")
                log("  This may require Administrator privileges")
                return False
        except Exception as sdk_err:
            log(f"⚠ SDK check failed: {sdk_err}")
            log("Trying direct Device Manager search...")
        
        # Fallback: Search Device Manager directly
        zwo_devices = _find_zwo_usb_devices(logger=logger)
        
        if not zwo_devices:
            log("✗ No ZWO USB devices found in Device Manager")
            return False
        
        # Filter by camera name if provided
        if camera_name:
            model = camera_name.replace("ZWO ", "").strip()
            log(f"Looking for specific camera: {model}")
            matched = [d for d in zwo_devices if model.upper() in d['description'].upper()]
            
            if not matched:
                log(f"✗ Camera '{model}' not found in USB device list")
                log(f"Available cameras: {', '.join(d['description'] for d in zwo_devices)}")
                return False
            
            devices_to_reset = matched
            log(f"Found {len(matched)} matching device(s)")
        else:
            devices_to_reset = zwo_devices
            log(f"⚠ No camera name specified - resetting ALL {len(zwo_devices)} ZWO devices")
        
        # Reset each device
        success_count = 0
        for device in devices_to_reset:
            log(f"Resetting: {device['description']} (DevInst: 0x{device['devinst']:08X})")
            
            if _reset_device(device['devinst'], logger=logger):
                success_count += 1
                log(f"  ✓ Reset requested successfully")
            else:
                log(f"  ✗ Reset request failed")
        
        if success_count > 0:
            log(f"✓ USB reset completed for {success_count}/{len(devices_to_reset)} device(s)")
            log("Waiting 3 seconds for device re-enumeration...")
            time.sleep(3)
            return True
        else:
            log("✗ All reset attempts failed")
            return False
            
    except Exception as e:
        log(f"✗ Error during USB reset: {e}")
        import traceback
        log(traceback.format_exc())
        return False


def _reset_device_by_description(description: str, logger=None) -> bool:
    """
    Find and reset a device by its description string.
    
    Uses CM_Locate_DevNode to find device by name, which is more reliable
    than enumerating all devices.
    """
    def log(msg):
        if logger:
            logger(msg)
    
    try:
        # Create a device instance string from description
        # Format: "USB\VID_03C3&PID_XXXX\SerialNumber" or search by friendly name
        
        # Method: Use CM_Enumerate_Classes and CM_Get_Device_ID_List
        # to search for devices with matching description
        
        # This is complex - for now, return False and use the full enumeration
        # TODO: Implement more efficient CM API-based search
        return False
        
    except Exception as e:
        if log:
            log(f"Error in device search: {e}")
        return False


def _find_zwo_usb_devices(logger=None) -> List[dict]:
    """
    Find all ZWO USB devices connected to the system.
    
    Returns:
        List of dicts with 'devinst' and 'description' keys
    """
    def log(msg):
        if logger:
            logger(msg)
    
    devices = []
    
    try:
        if log:
            log("Searching for ZWO devices using Configuration Manager API...")
        
        # Use CM_Get_Device_ID_List to find devices matching ZWO Hardware ID pattern
        # This is much more efficient than enumerating all devices
        
        # Search patterns for ZWO cameras
        search_patterns = [
            "USB\\VID_03C3",  # All ZWO USB devices (backslash in pattern)
        ]
        
        for pattern in search_patterns:
            # Get required buffer size
            buffer_size = wintypes.ULONG(0)
            result = cfgmgr32.CM_Get_Device_ID_List_SizeW(
                ctypes.byref(buffer_size),
                ctypes.c_wchar_p(pattern),
                0x00000000  # CM_GETIDLIST_FILTER_ENUMERATOR
            )
            
            if result != CR_SUCCESS or buffer_size.value == 0:
                continue
            
            # Allocate buffer and get device ID list
            buffer = ctypes.create_unicode_buffer(buffer_size.value)
            result = cfgmgr32.CM_Get_Device_ID_ListW(
                ctypes.c_wchar_p(pattern),
                buffer,
                buffer_size,
                0x00000000  # CM_GETIDLIST_FILTER_ENUMERATOR
            )
            
            if result != CR_SUCCESS:
                if log:
                    log(f"CM_Get_Device_ID_ListW failed: 0x{result:08X}")
                continue
            
            # Parse multi-string buffer (null-separated strings, double-null terminated)
            device_ids = []
            current_pos = 0
            while current_pos < buffer_size.value:
                # Find next null terminator
                end_pos = current_pos
                while end_pos < buffer_size.value and buffer[end_pos] != '\0':
                    end_pos += 1
                
                if end_pos == current_pos:
                    break  # Double null = end of list
                
                device_id = buffer[current_pos:end_pos]
                if device_id:
                    device_ids.append(device_id)
                
                current_pos = end_pos + 1
            
            if log:
                log(f"Found {len(device_ids)} device(s) matching pattern: {pattern}")
            
            # Get device instance handle for each device ID
            for device_id in device_ids:
                try:
                    # Filter to only USB devices with ZWO VID
                    device_id_upper = device_id.upper()
                    if not (device_id_upper.startswith("USB\\") and "VID_03C3" in device_id_upper):
                        continue
                    
                    # Filter out known non-camera accessories
                    # 4001 = ST4 guide port, 1F10/1F20 = EAF focuser controls
                    skip_pids = ["PID_4001", "PID_1F10", "PID_1F20"]
                    if any(pid in device_id_upper for pid in skip_pids):
                        continue
                    
                    # Get device instance (devinst) from device ID
                    devinst = wintypes.DWORD(0)
                    result = cfgmgr32.CM_Locate_DevNodeW(
                        ctypes.byref(devinst),
                        ctypes.c_wchar_p(device_id),
                        CM_LOCATE_DEVNODE_NORMAL
                    )
                    
                    if result != CR_SUCCESS:
                        continue
                    
                    # Get device description using registry
                    desc_buffer = ctypes.create_unicode_buffer(256)
                    desc_size = wintypes.ULONG(ctypes.sizeof(desc_buffer))
                    
                    result = cfgmgr32.CM_Get_DevNode_Registry_PropertyW(
                        devinst,
                        0x00000001,  # CM_DRP_DEVICEDESC
                        None,
                        desc_buffer,
                        ctypes.byref(desc_size),
                        0
                    )
                    
                    description = desc_buffer.value if result == CR_SUCCESS else "ZWO Camera"
                    
                    devices.append({
                        'devinst': devinst.value,
                        'description': description,
                        'hardware_id': device_id
                    })
                    
                    if log:
                        log(f"  Found: {description}")
                        log(f"    Device ID: {device_id}")
                
                except Exception as e:
                    if log:
                        log(f"  Error processing device {device_id}: {e}")
        
        if log:
            log(f"Total: {len(devices)} ZWO camera(s) found")
    
    except Exception as e:
        if log:
            log(f"Error: {e}")
            import traceback
            log(traceback.format_exc())
    
    return devices


def _reset_device(devinst: int, logger=None) -> bool:
    """
    Request device restart via Windows Configuration Manager.
    
    Args:
        devinst: Device instance handle from SetupDiEnumDeviceInfo
        logger: Optional logger callback
        
    Returns:
        True if reset was successful
    """
    def log(msg):
        if logger:
            logger(msg)
    
    try:
        # Method 1: Request device re-enumeration (softest approach)
        # This asks Windows to reload the driver without fully removing the device
        result = cfgmgr32.CM_Reenumerate_DevNode(
            devinst,
            CM_REENUMERATE_NORMAL
        )
        
        if result == CR_SUCCESS:
            if log:
                log("  Device re-enumeration requested")
            return True
        else:
            if log:
                log(f"  CM_Reenumerate_DevNode failed: 0x{result:08X}")
            
            # Method 2: Try restart (stronger reset)
            result = cfgmgr32.CM_Setup_DevNode(
                devinst,
                0x00000004  # CM_SETUP_DEVNODE_RESET
            )
            
            if result == CR_SUCCESS:
                if log:
                    log("  Device restart requested")
                return True
            else:
                if log:
                    log(f"  CM_Setup_DevNode failed: 0x{result:08X}")
                return False
    
    except Exception as e:
        if log:
            log(f"  Error resetting device: {e}")
        return False


def is_usb_reset_available() -> bool:
    """
    Check if USB reset functionality is available on this system.
    
    Returns:
        True if Windows API is loaded successfully
    """
    return WINDOWS_API_AVAILABLE


# Convenience function for testing
if __name__ == "__main__":
    print("=== USB Reset Utility Test ===\n")
    
    if not is_usb_reset_available():
        print("✗ USB reset not available (not Windows or API load failed)")
        exit(1)
    
    print("Available: Yes")
    print("\nSearching for ZWO cameras...")
    
    # Test: Find and list ZWO devices
    devices = _find_zwo_usb_devices(logger=print)
    
    if not devices:
        print("\n✗ No ZWO cameras found")
        exit(1)
    
    print(f"\n✓ Found {len(devices)} ZWO device(s)")
    for i, dev in enumerate(devices):
        print(f"  [{i}] {dev['description']}")
        print(f"      HW ID: {dev['hardware_id']}")
    
    # Ask user if they want to test reset
    print("\n⚠ WARNING: Testing reset will temporarily disconnect cameras!")
    response = input("Proceed with test reset? [y/N]: ").strip().lower()
    
    if response == 'y':
        print("\nAttempting reset...")
        if reset_zwo_camera_usb(logger=print):
            print("\n✓ Reset test completed")
        else:
            print("\n✗ Reset test failed")
    else:
        print("\nTest cancelled")
