# Output Mode Feature

ASIOverlayWatchDog now supports multiple output modes for maximum flexibility in your imaging workflow.

## Output Modes

### 1. File Mode (Default)
**Save processed images to disk**

- Traditional file-based output
- Saves images to configured output directory
- Supports PNG (lossless) and JPEG formats
- Configurable filename patterns with tokens
- Perfect for archiving and offline processing

**Use Case**: Standard image capture and archiving workflow

---

### 2. Web Server Mode
**Serve latest image via HTTP**

Runs an HTTP server that serves the most recently processed image. Perfect for integration with NINA, web dashboards, or remote monitoring.

**Endpoints:**
- `/latest` - Latest processed image (PNG format)
- `/status` - Server status and metadata (JSON)

**Configuration:**
- **Host**: Interface to bind (0.0.0.0 for all, 127.0.0.1 for localhost only)
- **Port**: HTTP port (default 8080)
- **Image Path**: URL path for image endpoint (default /latest)

**Example URLs:**
```
http://localhost:8080/latest          # Latest image
http://localhost:8080/status          # Server status JSON
http://192.168.1.100:8080/latest      # From other devices on network
```

**Use Case**: 
- NINA integration for live all-sky monitoring
- Web dashboards displaying current sky conditions
- Remote monitoring from mobile devices
- Integration with automation systems

**NINA Setup:**
1. Go to Settings → Output Mode
2. Select "🌐 Web Server"
3. Configure host (use 0.0.0.0 to allow network access)
4. Set port (8080 or custom)
5. Click "✓ Apply All Settings"
6. Note the URL shown in status (e.g., http://0.0.0.0:8080/latest)
7. In NINA: Add "Image Viewer" → Set URL to server address

---

## Configuration

### Changing Output Mode

1. Open **Settings** tab
2. Locate **Output Mode** card at top
3. Select desired mode:
   - 💾 **Save to File** - Traditional file saving
   - 🌐 **Web Server** - HTTP server
4. Configure mode-specific settings (shown when selected)
5. Click **✓ Apply All Settings**
6. Status will show active URL when server running

### Mode-Specific Settings

Settings panels are shown/hidden automatically based on selected mode:

**File Mode:**
- No additional settings (uses standard output directory)

**Web Server:**
- Host, Port, Image Path

### Persistence

Output mode settings are saved to `config.json` and restored on app restart. If a server mode was active, you need to re-apply settings after restart to start the server.

---

## Integration Examples

### NINA Image Viewer
```
1. Settings → Output Mode → Web Server
2. Host: 0.0.0.0, Port: 8080
3. Apply Settings
4. NINA → Image Viewer → URL: http://<your-pc-ip>:8080/latest
5. Set refresh interval (e.g., 5 seconds)
```

### Web Dashboard
```html
<img src="http://192.168.1.100:8080/latest" 
     alt="All Sky" 
     onload="setTimeout(() => this.src = this.src.split('?')[0] + '?' + Date.now(), 5000)">
```

---

## Technical Details

### Web Server
- Built on Python `http.server` module
- Runs in daemon thread (non-blocking)
- Serves images from memory (no disk reads)
- PNG format for lossless quality
- Status endpoint returns JSON with uptime, image count, metadata

### Performance
- **Web Server**: Minimal overhead, serves from memory
- **File Mode**: Zero overhead (just disk writes)

### Compatibility
- **Web Server**: Works with any HTTP client (browsers, NINA, curl, wget)
- **File Mode**: Standard image files (PNG/JPEG)

---

## Troubleshooting

### Web Server Issues

**"Port already in use"**
- Another application is using the port
- Try a different port (e.g., 8081, 8082, etc.)
- Check with: `netstat -ano | findstr :8080` (Windows)

**"Connection refused" from other devices**
- Host must be `0.0.0.0` (not `127.0.0.1`)
- Check firewall rules (allow port 8080)
- Verify network connectivity

**404 errors**
- Check image path matches URL (default `/latest`)
- Server may not have processed any images yet
- Check logs for errors

---

## Best Practices

1. **File Mode**: Use for archiving, post-processing pipelines
2. **Web Server**: Use for dashboards, remote viewing, NINA integration
3. **Hybrid**: File mode always saves to disk, servers serve latest in addition
4. **Network**: Use 0.0.0.0 for LAN access, 127.0.0.1 for localhost only
5. **Firewall**: Allow configured ports through Windows Firewall
6. **Resolution**: Lower resize_percent in Processing settings to reduce bandwidth

---

## Feature Roadmap

Future enhancements under consideration:
- Simultaneous file + server modes
- WebSocket push for zero-latency updates
- HLS streaming for broader compatibility
- Multiple concurrent streams
- Authentication for web endpoints
- Bandwidth throttling
- Image compression options for web mode
