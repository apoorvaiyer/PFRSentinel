# PFR Sentinel v3.1.3

[Brief description of what's new in this release]

## 🚀 New Features
- [Feature 1]
- [Feature 2]

## 🐛 Bug Fixes
- [Fix 1]
- [Fix 2]

## ⚡ Improvements
- Disabled UPX compression to reduce antivirus false positives
- [Other improvements]

## 📥 Download

**Installer:** [PFR Sentinel-3.1.3-setup.exe](link)

### 🔒 Security Notice

**VirusTotal Scan Results:** [X/72 detections](https://www.virustotal.com/gui/file/HASH_HERE)

⚠️ **Windows Defender Warning:** This installer is **unsigned** (code signing costs $200-500/year). Windows may show a SmartScreen warning - this is normal for open source software.

**The detections are false positives:**
- Unsigned PyInstaller executable triggers heuristic analysis
- No actual malware signatures detected
- Source code is fully available for review

**If you're concerned:**
- ✅ Review the [VirusTotal scan results](link) showing XX/72 engines report clean
- ✅ Check the [source code](https://github.com/USERNAME/PFRSentinel) 
- ✅ Build from source using `build_sentinel_installer.bat`

**To install:** Click "More info" → "Run anyway" when Windows Defender shows the SmartScreen warning.

## 📋 Requirements

- Windows 10/11 (64-bit)
- ZWO ASI camera (optional - for direct capture mode)
- 300 MB disk space

## 🔄 Upgrading from Previous Version

The installer automatically:
- ✅ Preserves your settings in `%APPDATA%\PFRSentinel\config.json`
- ✅ Keeps your logs in `%APPDATA%\PFRSentinel\logs\`
- ✅ Maintains overlay configurations

Simply run the new installer - no uninstall needed.

## 📚 Documentation

- [Quick Start Guide](docs/QUICKSTART.md)
- [Full Documentation](docs/README.md)
- [ZWO Camera Setup](docs/ZWO_SETUP_GUIDE.md)
- [VirusTotal Scanning](docs/dev/VIRUSTOTAL_SCANNING.md)

## 🔧 Known Issues

- [Known issue 1]
- [Known issue 2]

## 📝 Full Changelog

[Detailed changes since last version]

---

**Full Release Notes:** [CHANGELOG.md](link)
