<#
  Connect-SimplySign.ps1
  ----------------------
  Automates Certum SimplySign Desktop authentication by generating TOTP
  and sending it via simulated keystrokes.
  
  Based on: https://www.devas.life/how-to-automate-signing-your-windows-app-with-certum/
  
  Environment Variables Required:
    CERTUM_OTP_URI   - The otpauth:// URI from your SimplySign QR code
    
  Optional:
    CERTUM_EXE_PATH  - Path to SimplySign Desktop executable
                       (defaults to standard install location)
#>

param(
    [switch]$SkipIfConnected
)

# === 1. SETTINGS ============================================================
$OtpUri = $env:CERTUM_OTP_URI
$ExePath = $env:CERTUM_EXE_PATH

# Default SimplySign Desktop path if not specified
if (-not $ExePath) {
    $ExePath = "${env:ProgramFiles}\Certum\SimplySign Desktop\SimplySignDesktop.exe"
    if (-not (Test-Path $ExePath)) {
        $ExePath = "${env:LOCALAPPDATA}\Certum\SimplySign Desktop\SimplySignDesktop.exe"
    }
    if (-not (Test-Path $ExePath)) {
        $ExePath = "${env:ProgramFiles(x86)}\Certum\SimplySign Desktop\SimplySignDesktop.exe"
    }
}

# Validate required settings
if (-not $OtpUri) {
    Write-Host "ERROR: CERTUM_OTP_URI environment variable not set" -ForegroundColor Red
    Write-Host ""
    Write-Host "To set it, run:" -ForegroundColor Yellow
    Write-Host '  $env:CERTUM_OTP_URI = "otpauth://totp/..."'
    Write-Host ""
    Write-Host "Or set it permanently:" -ForegroundColor Yellow
    Write-Host '  [Environment]::SetEnvironmentVariable("CERTUM_OTP_URI", "otpauth://totp/...", "User")'
    exit 1
}

if (-not (Test-Path $ExePath)) {
    Write-Host "ERROR: SimplySign Desktop not found at: $ExePath" -ForegroundColor Red
    Write-Host ""
    Write-Host "Set CERTUM_EXE_PATH to the correct location" -ForegroundColor Yellow
    exit 1
}

# === 2. CHECK IF ALREADY CONNECTED ==========================================
if ($SkipIfConnected) {
    # Check if certificate is accessible (indicates SimplySign is connected)
    $cert = Get-ChildItem -Path Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue | 
            Where-Object { $_.Thumbprint -eq "B5E267FE814CD41B883876712CA326C288FB3492" }
    if ($cert) {
        Write-Host "SimplySign already connected (certificate accessible)" -ForegroundColor Green
        exit 0
    }
}

# === 3. PARSE THE otpauth:// URI ============================================
$uri = [Uri]$OtpUri

# Parse query string
$q = @{}
foreach ($part in $uri.Query.TrimStart('?') -split '&') {
    $kv = $part -split '=', 2
    if ($kv.Count -eq 2) { 
        $q[$kv[0]] = [Uri]::UnescapeDataString($kv[1]) 
    }
}

$Base32 = $q['secret']
$Digits = if ($q['digits']) { [int]$q['digits'] } else { 6 }
$Period = if ($q['period']) { [int]$q['period'] } else { 30 }
$Algorithm = if ($q['algorithm']) { $q['algorithm'].ToUpper() } else { 'SHA1' }

if (-not $Base32) {
    Write-Host "ERROR: Could not extract secret from OTP URI" -ForegroundColor Red
    exit 1
}

if ($Algorithm -notin @('SHA1', 'SHA256', 'SHA512')) {
    Write-Host "ERROR: Unsupported algorithm: $Algorithm (supported: SHA1, SHA256, SHA512)" -ForegroundColor Red
    exit 1
}

# === 4. TOTP GENERATOR ======================================================
Add-Type -Language CSharp @"
using System;
using System.Security.Cryptography;

public static class Totp
{
    private const string B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

    private static byte[] Base32Decode(string s)
    {
        s = s.TrimEnd('=').ToUpperInvariant();
        int byteCount = s.Length * 5 / 8;
        byte[] bytes = new byte[byteCount];

        int bitBuffer = 0, bitsLeft = 0, idx = 0;
        foreach (char c in s)
        {
            int val = B32.IndexOf(c);
            if (val < 0) throw new ArgumentException("Invalid Base32 char: " + c);

            bitBuffer = (bitBuffer << 5) | val;
            bitsLeft += 5;

            if (bitsLeft >= 8)
            {
                bytes[idx++] = (byte)(bitBuffer >> (bitsLeft - 8));
                bitsLeft -= 8;
            }
        }
        return bytes;
    }

    public static string Now(string secret, int digits, int period, string algorithm)
    {
        byte[] key = Base32Decode(secret);
        long counter = DateTimeOffset.UtcNow.ToUnixTimeSeconds() / period;

        byte[] cnt = BitConverter.GetBytes(counter);
        if (BitConverter.IsLittleEndian) Array.Reverse(cnt);

        HMAC hmac;
        switch (algorithm.ToUpper())
        {
            case "SHA256":
                hmac = new HMACSHA256(key);
                break;
            case "SHA512":
                hmac = new HMACSHA512(key);
                break;
            default:
                hmac = new HMACSHA1(key);
                break;
        }

        byte[] hash = hmac.ComputeHash(cnt);
        int offset = hash[hash.Length - 1] & 0x0F;
        int binary =
            ((hash[offset] & 0x7F) << 24) |
            ((hash[offset + 1] & 0xFF) << 16) |
            ((hash[offset + 2] & 0xFF) << 8) |
            (hash[offset + 3] & 0xFF);

        int otp = binary % (int)Math.Pow(10, digits);
        return otp.ToString(new string('0', digits));
    }
}
"@

function Get-TotpCode {
    param(
        [string]$Secret,
        [int]$Digits = 6,
        [int]$Period = 30,
        [string]$Algorithm = 'SHA1'
    )
    [Totp]::Now($Secret, $Digits, $Period, $Algorithm)
}

# === 5. LAUNCH SimplySign AND SEND CREDENTIALS ==============================
$otp = Get-TotpCode -Secret $Base32 -Digits $Digits -Period $Period -Algorithm $Algorithm
Write-Host "Generated TOTP code (using $Algorithm)" -ForegroundColor Cyan

# Check if SimplySign is already running
$existingProc = Get-Process -Name "SimplySignDesktop" -ErrorAction SilentlyContinue

if ($existingProc) {
    Write-Host "SimplySign Desktop already running (PID: $($existingProc.Id))" -ForegroundColor Yellow
    $proc = $existingProc
} else {
    Write-Host "Starting SimplySign Desktop..." -ForegroundColor Cyan
    $proc = Start-Process -FilePath $ExePath -PassThru
    Start-Sleep -Seconds 3
}

Write-Host "Clicking SimplySign tray icon to open login window..." -ForegroundColor Cyan

# Load UI Automation assembly
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

# Function to click a point on screen
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public class MouseClicker
{
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetCursorPos(int X, int Y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, int dwExtraInfo);

    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP = 0x0010;

    public static void Click(int x, int y)
    {
        SetCursorPos(x, y);
        mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0);
        mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0);
    }
    
    public static void RightClick(int x, int y)
    {
        SetCursorPos(x, y);
        mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0);
        mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0);
    }
}
"@

# Find the system tray and click SimplySign icon
$trayClicked = $false

try {
    # Get the taskbar
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $taskbar = $root.FindFirst(
        [System.Windows.Automation.TreeScope]::Children,
        (New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ClassNameProperty, "Shell_TrayWnd"
        ))
    )

    if ($taskbar) {
        # SimplySign is typically in hidden icons, so go straight there
        $chevron = $taskbar.FindFirst(
            [System.Windows.Automation.TreeScope]::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::NameProperty, "Show Hidden Icons"
            ))
        )
        
        if ($chevron) {
            Write-Host "Opening hidden icons..." -ForegroundColor Cyan
            $rect = $chevron.Current.BoundingRectangle
            $x = [int]($rect.X + $rect.Width / 2)
            $y = [int]($rect.Y + $rect.Height / 2)
            [MouseClicker]::Click($x, $y)
            Start-Sleep -Seconds 1
            
            # Now find the overflow window (Windows 11 uses TopLevelWindowForOverflowXamlIsland)
            $overflow = $root.FindFirst(
                [System.Windows.Automation.TreeScope]::Children,
                (New-Object System.Windows.Automation.PropertyCondition(
                    [System.Windows.Automation.AutomationElement]::ClassNameProperty, "TopLevelWindowForOverflowXamlIsland"
                ))
            )
            
            # Fallback for older Windows
            if (-not $overflow) {
                $overflow = $root.FindFirst(
                    [System.Windows.Automation.TreeScope]::Children,
                    (New-Object System.Windows.Automation.PropertyCondition(
                        [System.Windows.Automation.AutomationElement]::ClassNameProperty, "NotifyIconOverflowWindow"
                    ))
                )
            }
            
            if ($overflow) {
                $icons = $overflow.FindAll(
                    [System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition
                )
                
                foreach ($icon in $icons) {
                    $name = $icon.Current.Name
                    if ($name -like "*SimplySign*") {
                        Write-Host "Found tray icon in overflow: $name" -ForegroundColor Green
                        $rect = $icon.Current.BoundingRectangle
                        if ($rect.Width -gt 0 -and $rect.Height -gt 0) {
                            $iconX = [int]($rect.X + $rect.Width / 2)
                            $iconY = [int]($rect.Y + $rect.Height / 2)
                            
                            # Right-click to open context menu
                            Write-Host "Right-clicking to open context menu..." -ForegroundColor Cyan
                            [MouseClicker]::RightClick($iconX, $iconY)
                            Start-Sleep -Milliseconds 800
                            
                            # Use keyboard to select "Connect to SimplySign" (first menu item)
                            Write-Host "Selecting 'Connect to SimplySign' via keyboard..." -ForegroundColor Cyan
                            $wshellMenu = New-Object -ComObject WScript.Shell
                            Start-Sleep -Milliseconds 300
                            $wshellMenu.SendKeys("{DOWN}")
                            Start-Sleep -Milliseconds 200
                            $wshellMenu.SendKeys("{ENTER}")
                            $trayClicked = $true
                            
                            Start-Sleep -Milliseconds 500
                            break
                        }
                    }
                }
            }
        }
    }
} catch {
    Write-Host "UI Automation error: $_" -ForegroundColor Yellow
}

if (-not $trayClicked) {
    Write-Host "Could not find SimplySign tray icon automatically" -ForegroundColor Yellow
    Write-Host "Please click the SimplySign icon in the system tray manually" -ForegroundColor Cyan
    Write-Host "Then enter this code: $otp" -ForegroundColor Green
    exit 1
}

Write-Host "Waiting for login window..." -ForegroundColor Cyan
Start-Sleep -Seconds 2

$wshell = New-Object -ComObject WScript.Shell

# Try to focus the window using multiple methods
$focused = $false

# Method 1: Try by process ID
for ($i = 0; $i -lt 10; $i++) {
    $focused = $wshell.AppActivate($proc.Id)
    if ($focused) { break }
    Start-Sleep -Milliseconds 300
}

# Method 2: Try various window title patterns
if (-not $focused) {
    $titles = @('SimplySign Desktop', 'SimplySign', 'Certum', 'Login')
    foreach ($title in $titles) {
        $focused = $wshell.AppActivate($title)
        if ($focused) { break }
    }
}

if (-not $focused) {
    Write-Host "Could not focus login window" -ForegroundColor Yellow
    Write-Host "Please enter this code manually: $otp" -ForegroundColor Green
    exit 1
}

# Window has focus - send the OTP
Start-Sleep -Milliseconds 500
$wshell.SendKeys("$otp{ENTER}")

Write-Host ""
Write-Host "Credentials sent to SimplySign Desktop" -ForegroundColor Green
Write-Host "Waiting for cloud smart-card to mount..." -ForegroundColor Cyan

# Initial wait for SimplySign to authenticate with cloud HSM
Start-Sleep -Seconds 5

# Wait for certificate to become available
$maxWait = 30
for ($i = 0; $i -lt $maxWait; $i++) {
    Start-Sleep -Seconds 1
    $cert = Get-ChildItem -Path Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
            Where-Object { $_.Thumbprint -eq "B5E267FE814CD41B883876712CA326C288FB3492" }
    if ($cert) {
        Write-Host ""
        Write-Host "SimplySign connected successfully!" -ForegroundColor Green
        exit 0
    }
    Write-Host "." -NoNewline
}

Write-Host ""
Write-Host "WARNING: Timed out waiting for certificate" -ForegroundColor Yellow
Write-Host "SimplySign may still be connecting - check the tray icon" -ForegroundColor Yellow
exit 0
