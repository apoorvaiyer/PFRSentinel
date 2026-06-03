"""
Test USB Reset Functionality
=============================

Quick test to verify USB reset works for ZWO cameras.
Run this to check if the USB reset capability is available on your system.

Usage:
    python test_usb_reset.py
"""

import sys
import os

# Add project root (parent of scripts/dev) to path so we can import services.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.usb_reset_win import reset_zwo_camera_usb, is_usb_reset_available, _find_zwo_usb_devices


def main():
    print("=" * 70)
    print("ZWO Camera USB Reset Test")
    print("=" * 70)
    print()
    
    # Check if USB reset is available
    if not is_usb_reset_available():
        print("✗ USB reset NOT available on this system")
        print()
        print("Possible reasons:")
        print("  - Not running on Windows")
        print("  - Windows API DLLs not available")
        print("  - ctypes module not working")
        print()
        return 1
    
    print("✓ USB reset capability: AVAILABLE")
    print()
    
    # Find ZWO devices
    print("Searching for ZWO cameras...")
    print()
    
    devices = _find_zwo_usb_devices(logger=print)
    
    if not devices:
        print()
        print("✗ No ZWO cameras found")
        print()
        print("Check:")
        print("  - Camera is connected via USB")
        print("  - Camera has power")
        print("  - Windows Device Manager shows the camera")
        print()
        return 1
    
    print()
    print(f"✓ Found {len(devices)} ZWO camera(s):")
    print()
    
    for i, dev in enumerate(devices):
        print(f"  [{i}] {dev['description']}")
        print(f"      Hardware ID: {dev['hardware_id']}")
        print(f"      Device Instance: 0x{dev['devinst']:08X}")
        print()
    
    # Ask if user wants to test reset
    print("=" * 70)
    print("Test Reset")
    print("=" * 70)
    print()
    print("⚠ WARNING: This will temporarily disconnect the camera!")
    print("  - Any running capture will be interrupted")
    print("  - Camera will reconnect after 3-5 seconds")
    print("  - Close any apps using the camera first")
    print()
    
    response = input("Proceed with test reset? [y/N]: ").strip().lower()
    
    if response != 'y':
        print()
        print("Test cancelled. No changes made.")
        return 0
    
    print()
    print("=" * 70)
    print("Performing Reset Test")
    print("=" * 70)
    print()
    
    # Test reset (all cameras)
    if reset_zwo_camera_usb(logger=print):
        print()
        print("=" * 70)
        print("✓ Test Complete - Reset Successful")
        print("=" * 70)
        print()
        print("The camera(s) should now be reconnecting.")
        print("Check Device Manager or try connecting in your application.")
        print()
        return 0
    else:
        print()
        print("=" * 70)
        print("✗ Test Failed - Reset Not Successful")
        print("=" * 70)
        print()
        print("Possible issues:")
        print("  - Insufficient permissions (try running as Administrator)")
        print("  - Camera driver not responding")
        print("  - Device in use by another application")
        print()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nTest cancelled by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
