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
CM_LOCATE_DEVNODE_PHANTOM = 0x00000001  # Find disabled/phantom device nodes
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
    Reset USB connection for a specific ZWO camera by requesting device re-enumeration.
    
    This forces Windows to reload the USB driver, which can recover cameras
    stuck in bad states after exposure failures.
    
    Args:
        camera_name: Camera name (e.g., "ZWO ASI676MC") to target.
                    REQUIRED to prevent accidentally resetting other cameras.
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
    
    if not camera_name:
        log("⚠ No camera name specified - refusing to reset ALL ZWO devices")
        log("  A specific camera name is required to avoid disrupting other cameras")
        return False
    
    log(f"=== Attempting USB Device Reset for: {camera_name} ===")
    
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
        
        # Filter to only the target camera
        model = camera_name.replace("ZWO ", "").strip()
        log(f"Looking for specific camera: {model}")
        matched = [d for d in zwo_devices if model.upper() in d['description'].upper()]
        
        if not matched:
            log(f"✗ Camera '{model}' not found in USB device list")
            log(f"Available cameras: {', '.join(d['description'] for d in zwo_devices)}")
            return False
        
        devices_to_reset = matched
        skipped = [d for d in zwo_devices if d not in matched]
        log(f"Target: {', '.join(d['description'] for d in matched)}")
        if skipped:
            log(f"Leaving alone: {', '.join(d['description'] for d in skipped)}")
        
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


def _is_admin() -> bool:
    """Check if current process has Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
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


def disable_enable_zwo_camera_usb(camera_name: str,
                                   disable_seconds: int = 15,
                                   logger=None) -> bool:
    """
    Disable and re-enable a SPECIFIC ZWO camera USB device via Windows Device Manager API.

    Mimics the manual process of:
      1. Device Manager -> Right-click device -> Disable device
      2. Wait N seconds (allows USB hardware state to fully reset)
      3. Device Manager -> Right-click device -> Enable device

    This is more aggressive than CM_Reenumerate_DevNode and can recover cameras
    stuck in a bad USB state that persists across soft resets and SDK reinit.

    IMPORTANT: Only affects the specific camera identified by camera_name.
    Other ZWO devices (guide cameras, etc.) are left untouched.

    Args:
        camera_name: Camera name to target (e.g. "ZWO ASI676MC"). REQUIRED.
        disable_seconds: Seconds to keep device disabled (default 15, matches manual fix).
        logger: Logging callback.

    Returns:
        True if disable/enable cycle completed, False otherwise.

    Note:
        Requires Administrator privileges.
    """
    def log(msg):
        if logger:
            logger(msg)
        else:
            print(msg)

    if not WINDOWS_API_AVAILABLE:
        log("\u26a0 Windows API not available - USB disable/enable not supported")
        return False

    if not camera_name:
        log("\u26a0 No camera name specified - refusing to disable/enable ALL ZWO devices")
        log("  A specific camera name is required to avoid disrupting other cameras")
        return False

    if not _is_admin():
        log("\u26a0 Administrator privileges required for device disable/enable")
        log("  Run the application as Administrator to enable this recovery method")
        return False

    log("=== USB Device Disable/Enable (Device Manager Reset) ===")
    log(f"Target camera: {camera_name}")
    log(f"Disable duration: {disable_seconds} seconds")

    all_devices = _find_zwo_usb_devices(logger=logger)
    if not all_devices:
        log("\u2717 No ZWO USB devices found in Device Manager")
        return False

    # Filter to ONLY the target camera - never touch other devices
    model = camera_name.replace("ZWO ", "").strip()
    log(f"Looking for camera matching: {model}")
    devices = [d for d in all_devices if model.upper() in d['description'].upper()]
    skipped = [d for d in all_devices if d not in devices]
    if not devices:
        log(f"\u2717 No device matching '{model}' found")
        log(f"  Available: {', '.join(d['description'] for d in all_devices)}")
        return False
    if skipped:
        log(f"Leaving untouched: {', '.join(d['description'] for d in skipped)}")

    success = False
    for device in devices:
        devinst = device['devinst']
        device_id = device['hardware_id']
        desc = device['description']

        log(f"Disabling: {desc}")
        log(f"  Device ID: {device_id}")

        # Step 1: Disable the device
        result = cfgmgr32.CM_Disable_DevNode(devinst, 0)
        if result != CR_SUCCESS:
            log(f"  \u2717 CM_Disable_DevNode failed: 0x{result:08X}")
            log("  This requires Administrator privileges")
            continue

        log("  \u2713 Device disabled")
        log(f"  Waiting {disable_seconds} seconds for USB state to fully clear...")
        time.sleep(disable_seconds)

        # Step 2: Re-locate device node (device is phantom/disabled now)
        new_devinst = wintypes.DWORD(0)
        result = cfgmgr32.CM_Locate_DevNodeW(
            ctypes.byref(new_devinst),
            ctypes.c_wchar_p(device_id),
            CM_LOCATE_DEVNODE_PHANTOM
        )
        if result != CR_SUCCESS:
            log(f"  \u2717 Could not re-locate disabled device: 0x{result:08X}")
            log("  \u26a0 DEVICE MAY STAY DISABLED - check Device Manager!")
            continue

        # Step 3: Re-enable with retry
        max_enable_retries = 3
        enabled = False
        for retry in range(max_enable_retries):
            log(f"  Re-enabling device (attempt {retry + 1}/{max_enable_retries})...")
            result = cfgmgr32.CM_Enable_DevNode(new_devinst.value, 0)
            if result == CR_SUCCESS:
                log(f"  \u2713 Device re-enabled successfully")
                enabled = True
                break
            else:
                log(f"  \u2717 CM_Enable_DevNode failed: 0x{result:08X}")
                if retry < max_enable_retries - 1:
                    time.sleep(2)

        if not enabled:
            log(f"  \u26a0 FAILED to re-enable {desc} - manually enable in Device Manager!")
            continue

        success = True

    if success:
        log("Waiting 3 seconds for driver initialization...")
        time.sleep(3)
        log("\u2713 USB disable/enable cycle complete")
    else:
        log("\u2717 USB disable/enable failed for all devices")

    return success


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
    
    # Let user pick which camera to target
    if len(devices) == 1:
        target_idx = 0
        print(f"\nOnly one camera found, targeting: {devices[0]['description']}")
    else:
        print(f"\nWhich camera to reset? (0-{len(devices)-1})")
        try:
            target_idx = int(input("Camera number: ").strip())
            if target_idx < 0 or target_idx >= len(devices):
                print("Invalid selection")
                exit(1)
        except (ValueError, EOFError):
            print("Invalid selection")
            exit(1)
    
    target_name = devices[target_idx]['description']
    other_names = [d['description'] for i, d in enumerate(devices) if i != target_idx]
    print(f"\nTarget:     {target_name}")
    if other_names:
        print(f"Untouched:  {', '.join(other_names)}")
    
    # Choose reset method
    print("\n⚠ WARNING: This will temporarily disconnect the selected camera!")
    print("Options:")
    print("  1. Soft reset (CM_Reenumerate_DevNode - quick, ~3 seconds)")
    print("  2. Device Manager reset (Disable/Enable - ~20 seconds)")
    print("  N. Cancel")
    response = input("Choose [1/2/N]: ").strip().lower()
    
    if response == '1':
        print(f"\nSoft-resetting only: {target_name}")
        if reset_zwo_camera_usb(camera_name=target_name, logger=print):
            print("\n✓ Soft reset completed")
        else:
            print("\n✗ Soft reset failed")
    elif response == '2':
        if not _is_admin():
            print("\n✗ Administrator privileges required for disable/enable")
            print("  Re-run this script as Administrator")
        else:
            print(f"\nDisable/enable only: {target_name} (15 second wait)")
            if disable_enable_zwo_camera_usb(camera_name=target_name, logger=print):
                print("\n✓ Device Manager reset completed")
            else:
                print("\n✗ Device Manager reset failed")
    else:
        print("\nTest cancelled")
